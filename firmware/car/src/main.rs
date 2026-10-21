// firmware/car/src/main.rs
//! 麦克纳姆底盘阶段 1 固件：串口命令驱动四路电机开环运动，上报编码器遥测.
#![no_std]
#![no_main]

mod board;

use car_core::kinematics::WheelMap;
use car_core::line::{LineAssembler, LineEvent};
use car_core::motion::{MotionState, RunState};
use car_core::protocol::{
    Command, FrameBuf, ParseError, encode_ack, encode_boot, encode_error, encode_telemetry,
    parse_frame,
};
use car_core::speed::SpeedLoop;
use embassy_executor::Spawner;
use embassy_stm32::usart::BufferedUartRx;
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex;
use embassy_sync::channel::Channel;
use embassy_time::{Duration, Instant, Ticker};
use embedded_io_async::{Read, Write};
use panic_halt as _;

/// 固件版本，随开机帧上报.
const FIRMWARE_VERSION: u16 = 1;
/// 控制周期 10 ms；每 5 个周期上报一次遥测（20 Hz），每 50 个周期翻转 LED.
const CONTROL_PERIOD_MS: u64 = 10;
const TELEMETRY_EVERY: u32 = 5;
const LED_EVERY: u32 = 50;
/// 轮位（左前、右前、左后、右后）到电机的映射，2026-10-21 架空实测：
/// 左前 M4、右前 M1、左后 M3、右后 M2，四个电机正转时轮子均向前转.
const WHEEL_MAP: WheelMap = WheelMap::new([3, 0, 2, 1], [1, 1, 1, 1]);

/// 从某个串口收到的一行解析结果.
struct Incoming {
    port: u8,
    result: Result<Command, ParseError>,
}

static INCOMING: Channel<CriticalSectionRawMutex, Incoming, 8> = Channel::new();

/// 读取串口字节，组装成行并解析，结果交给控制循环.
#[embassy_executor::task(pool_size = 3)]
async fn rx_task(port: u8, mut rx: BufferedUartRx<'static>) {
    let mut assembler = LineAssembler::new();
    let mut chunk = [0_u8; 32];
    loop {
        // 读取错误（如帧错误、噪声）只丢弃本次数据，不终止任务
        let Ok(n) = rx.read(&mut chunk).await else {
            continue;
        };
        for &byte in &chunk[..n] {
            let result = match assembler.push(byte) {
                Some(LineEvent::Line(line)) => parse_frame(line),
                Some(LineEvent::Overflow) => {
                    Err(ParseError::Frame(car_core::protocol::ErrorReason::TooLong))
                }
                None => continue,
            };
            INCOMING.send(Incoming { port, result }).await;
        }
    }
}

#[embassy_executor::main]
async fn main(spawner: Spawner) {
    let (mut hw, receivers) = board::init();

    // ========== Step1: 三个候选通信口都发送开机帧，并启动接收任务 ==========
    let mut frame = FrameBuf::new();
    for (port, tx) in [(1_u8, &mut hw.tx1), (2, &mut hw.tx2), (3, &mut hw.tx3)] {
        if encode_boot(&mut frame, FIRMWARE_VERSION, port).is_ok() {
            let _ = tx.write_all(frame.as_bytes()).await;
        }
    }
    hw.log("car-fw boot, waiting for commands on USART1/USART2/USART3\r\n");
    for (port, rx) in (1_u8..).zip(receivers) {
        spawner.spawn(rx_task(port, rx).expect("rx_task 池容量为 3"));
    }

    // ========== Step2: 10 ms 控制循环：命令、看门狗、斜率、PWM、编码器、遥测 ==========
    let mut motion = MotionState::new(WHEEL_MAP);
    let mut speed_loop = SpeedLoop::new();
    let mut previous = hw.encoders.update();
    // 第一条有效帧所在的端口成为通信口，此后忽略另一口，避免无线模块干扰
    let mut locked: Option<u8> = None;
    let mut ticker = Ticker::every(Duration::from_millis(CONTROL_PERIOD_MS));
    let mut tick: u32 = 0;
    loop {
        let now_ms = Instant::now().as_millis() as u32;
        while let Ok(Incoming { port, result }) = INCOMING.try_receive() {
            if locked.is_none() && result.is_ok() {
                locked = Some(port);
                hw.log(match port {
                    1 => "command port locked: USART1\r\n",
                    2 => "command port locked: USART2\r\n",
                    _ => "command port locked: USART3\r\n",
                });
            }
            if locked != Some(port) {
                continue;
            }
            let encoded = match result {
                Ok(command) => {
                    let code = motion.apply(&command, now_ms);
                    if matches!(command, Command::Stop { .. }) {
                        // 停止同时清空积分并解除闭环故障
                        speed_loop.reset();
                    }
                    encode_ack(&mut frame, command.seq(), code)
                }
                Err(ParseError::Reject { seq, code }) => encode_ack(&mut frame, seq, code as u8),
                Err(ParseError::Frame(reason)) => encode_error(&mut frame, reason as u8),
            };
            if encoded.is_ok() {
                let _ = hw.tx(port).write_all(frame.as_bytes()).await;
            }
        }

        // ========== Step3: 目标转速经 PI 闭环得到占空比；闭环故障时锁定停车 ==========
        let encoders = hw.encoders.update();
        let deltas: [i32; 4] = core::array::from_fn(|i| encoders[i].wrapping_sub(previous[i]));
        previous = encoders;
        let targets = motion.tick(now_ms);
        let duties = speed_loop.update(targets, deltas);
        if speed_loop.fault() && motion.run_state() != RunState::Fault {
            motion.set_fault();
            hw.log("speed loop fault: stall or encoder failure, send $S to clear\r\n");
        }
        hw.motors.set(duties);

        if tick.is_multiple_of(TELEMETRY_EVERY)
            && let Some(port) = locked
            && encode_telemetry(
                &mut frame,
                now_ms,
                motion.run_state() as u8,
                encoders,
                duties,
            )
            .is_ok()
        {
            let _ = hw.tx(port).write_all(frame.as_bytes()).await;
        }
        if tick.is_multiple_of(LED_EVERY) {
            hw.led.toggle();
        }
        tick = tick.wrapping_add(1);
        ticker.next().await;
    }
}
