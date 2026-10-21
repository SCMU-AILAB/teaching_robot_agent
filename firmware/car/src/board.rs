// firmware/car/src/board.rs
//! MINDA-01 板级初始化：时钟、电机 PWM、编码器、串口、LED 与蜂鸣器.
//!
//! 引脚由原固件反汇编和模拟执行得到，见 docs/superpowers/specs/2026-10-21-car-base-phase1-design.md。

use embassy_stm32::gpio::{Level, Output, OutputType, Pull, Speed};
use embassy_stm32::peripherals::{
    TIM1, TIM2, TIM3, TIM4, TIM5, TIM9, TIM10, TIM11, USART2, USART3,
};
use embassy_stm32::time::Hertz;
use embassy_stm32::timer::low_level::CountingMode;
use embassy_stm32::timer::qei::{Config as QeiConfig, Qei};
use embassy_stm32::timer::simple_pwm::{PwmPin, SimplePwm};
use embassy_stm32::timer::{Channel, GeneralInstance4Channel};
use embassy_stm32::usart::{
    BufferedInterruptHandler, BufferedUart, BufferedUartRx, BufferedUartTx, Config as UartConfig,
    UartTx,
};
use embassy_stm32::{bind_interrupts, mode::Blocking};
use static_cell::StaticCell;

bind_interrupts!(struct Irqs {
    USART2 => BufferedInterruptHandler<USART2>;
    USART3 => BufferedInterruptHandler<USART3>;
});

/// 电机 PWM 频率，与原固件一致.
const PWM_HZ: u32 = 10_000;
/// 占空比千分比的分母.
const PERMILLE: u32 = 1000;
/// 原固件中各电机编码器的计数方向.
const ENCODER_SIGN: [i32; 4] = [-1, -1, 1, 1];

/// 写入一路 PWM 的占空千分比.
fn write_channel<T: GeneralInstance4Channel>(
    pwm: &mut SimplePwm<'static, T>,
    ch: Channel,
    permille: u32,
) {
    pwm.channel(ch)
        .set_duty_cycle_fraction(permille.min(PERMILLE), PERMILLE);
}

/// 把带符号占空比拆成正转、反转两路：正值只驱动正转引脚，负值只驱动反转引脚.
fn split_duty(duty: i16) -> (u32, u32) {
    let magnitude = u32::from(duty.unsigned_abs());
    if duty >= 0 {
        (magnitude, 0)
    } else {
        (0, magnitude)
    }
}

/// 四路电机 PWM.
pub struct Motors {
    tim1: SimplePwm<'static, TIM1>,
    tim9: SimplePwm<'static, TIM9>,
    tim10: SimplePwm<'static, TIM10>,
    tim11: SimplePwm<'static, TIM11>,
}

impl Motors {
    /// 设置 M1..M4 的占空千分比.
    pub fn set(&mut self, duties: [i16; 4]) {
        let [m1, m2, m3, m4] = duties.map(split_duty);
        // M1：TIM1 CH3 正转、CH4 反转
        write_channel(&mut self.tim1, Channel::Ch3, m1.0);
        write_channel(&mut self.tim1, Channel::Ch4, m1.1);
        // M2：TIM1 CH1 正转、CH2 反转
        write_channel(&mut self.tim1, Channel::Ch1, m2.0);
        write_channel(&mut self.tim1, Channel::Ch2, m2.1);
        // M3：TIM9 CH1 正转、CH2 反转
        write_channel(&mut self.tim9, Channel::Ch1, m3.0);
        write_channel(&mut self.tim9, Channel::Ch2, m3.1);
        // M4：TIM11 CH1（PB9）正转、TIM10 CH1（PB8）反转
        write_channel(&mut self.tim11, Channel::Ch1, m4.0);
        write_channel(&mut self.tim10, Channel::Ch1, m4.1);
    }
}

/// 四路正交编码器，16 位计数按差值累加为 32 位.
pub struct Encoders {
    m1: Qei<'static, TIM5>,
    m2: Qei<'static, TIM2>,
    m3: Qei<'static, TIM4>,
    m4: Qei<'static, TIM3>,
    last: [u16; 4],
    total: [i32; 4],
}

impl Encoders {
    /// 读取计数并返回 M1..M4 的累计值；需在计数器回绕半圈之前调用.
    pub fn update(&mut self) -> [i32; 4] {
        let counts = [
            self.m1.count(),
            self.m2.count(),
            self.m3.count(),
            self.m4.count(),
        ];
        for i in 0..4 {
            let delta = i32::from(counts[i].wrapping_sub(self.last[i]) as i16);
            self.last[i] = counts[i];
            self.total[i] = self.total[i].wrapping_add(delta * ENCODER_SIGN[i]);
        }
        self.total
    }
}

/// 初始化后的全部板级资源.
pub struct Board {
    pub motors: Motors,
    pub encoders: Encoders,
    pub led: Output<'static>,
    pub tx2: BufferedUartTx<'static>,
    pub tx3: BufferedUartTx<'static>,
    log: UartTx<'static, Blocking>,
    _buzzer: Output<'static>,
}

impl Board {
    /// 返回指定通信口的发送端；端口号只有 2 和 3.
    pub fn tx(&mut self, port: u8) -> &mut BufferedUartTx<'static> {
        if port == 2 {
            &mut self.tx2
        } else {
            &mut self.tx3
        }
    }

    /// 经 USART1（板上 UART1 口）输出调试文本.
    pub fn log(&mut self, text: &str) {
        let _ = self.log.blocking_write(text.as_bytes());
    }
}

/// 配置 168 MHz 时钟并初始化全部外设；另行返回 USART2、USART3 的接收端，供接收任务独占.
pub fn init() -> (Board, BufferedUartRx<'static>, BufferedUartRx<'static>) {
    use embassy_stm32::rcc::{
        AHBPrescaler, APBPrescaler, Pll, PllMul, PllPDiv, PllPreDiv, PllQDiv, PllSource, Sysclk,
    };

    // ========== Step1: HSI 16 MHz 经 PLL 倍频到 168 MHz，不依赖外部晶振频率 ==========
    let mut config = embassy_stm32::Config::default();
    config.rcc.pll_src = PllSource::HSI;
    config.rcc.pll = Some(Pll {
        prediv: PllPreDiv::DIV16,
        mul: PllMul::MUL336,
        divp: Some(PllPDiv::DIV2),
        divq: Some(PllQDiv::DIV7),
        divr: None,
    });
    config.rcc.sys = Sysclk::PLL1_P;
    config.rcc.ahb_pre = AHBPrescaler::DIV1;
    config.rcc.apb1_pre = APBPrescaler::DIV4;
    config.rcc.apb2_pre = APBPrescaler::DIV2;
    let p = embassy_stm32::init(config);

    // ========== Step2: 蜂鸣器静音、LED，以及先拉低 PWM 引脚的电机驱动 ==========
    let buzzer = Output::new(p.PA8, Level::Low, Speed::Low);
    let led = Output::new(p.PE10, Level::Low, Speed::Low);
    let freq = Hertz(PWM_HZ);
    let mode = CountingMode::EdgeAlignedUp;
    let pp = OutputType::PushPull;
    let mut tim1 = SimplePwm::new(
        p.TIM1,
        Some(PwmPin::new(p.PE9, pp)),
        Some(PwmPin::new(p.PE11, pp)),
        Some(PwmPin::new(p.PE13, pp)),
        Some(PwmPin::new(p.PE14, pp)),
        freq,
        mode,
    );
    let mut tim9 = SimplePwm::new(
        p.TIM9,
        Some(PwmPin::new(p.PE5, pp)),
        Some(PwmPin::new(p.PE6, pp)),
        None,
        None,
        freq,
        mode,
    );
    let mut tim10 = SimplePwm::new(
        p.TIM10,
        Some(PwmPin::new(p.PB8, pp)),
        None,
        None,
        None,
        freq,
        mode,
    );
    let mut tim11 = SimplePwm::new(
        p.TIM11,
        Some(PwmPin::new(p.PB9, pp)),
        None,
        None,
        None,
        freq,
        mode,
    );
    for ch in [Channel::Ch1, Channel::Ch2, Channel::Ch3, Channel::Ch4] {
        write_channel(&mut tim1, ch, 0);
        tim1.channel(ch).enable();
    }
    for ch in [Channel::Ch1, Channel::Ch2] {
        write_channel(&mut tim9, ch, 0);
        tim9.channel(ch).enable();
    }
    write_channel(&mut tim10, Channel::Ch1, 0);
    tim10.channel(Channel::Ch1).enable();
    write_channel(&mut tim11, Channel::Ch1, 0);
    tim11.channel(Channel::Ch1).enable();

    // ========== Step3: 编码器（霍尔输出加内部上拉）==========
    let qei = QeiConfig {
        ch1_pull: Pull::Up,
        ch2_pull: Pull::Up,
        ..QeiConfig::default()
    };
    let m1 = Qei::new(p.TIM5, p.PA0, p.PA1, qei);
    let m2 = Qei::new(p.TIM2, p.PA15, p.PB3, qei);
    let m3 = Qei::new(p.TIM4, p.PB6, p.PB7, qei);
    let m4 = Qei::new(p.TIM3, p.PB4, p.PB5, qei);
    let last = [m1.count(), m2.count(), m3.count(), m4.count()];

    // ========== Step4: 串口：USART1 调试日志，USART2/USART3 为候选通信口 ==========
    let uart = UartConfig::default();
    let log = UartTx::new_blocking(p.USART1, p.PA9, uart).expect("USART1 默认配置有效");
    static TX2: StaticCell<[u8; 256]> = StaticCell::new();
    static RX2: StaticCell<[u8; 256]> = StaticCell::new();
    static TX3: StaticCell<[u8; 256]> = StaticCell::new();
    static RX3: StaticCell<[u8; 256]> = StaticCell::new();
    let usart2 = BufferedUart::new(
        p.USART2,
        p.PD6,
        p.PD5,
        TX2.init([0; 256]),
        RX2.init([0; 256]),
        Irqs,
        uart,
    )
    .expect("USART2 默认配置有效");
    let usart3 = BufferedUart::new(
        p.USART3,
        p.PD9,
        p.PD8,
        TX3.init([0; 256]),
        RX3.init([0; 256]),
        Irqs,
        uart,
    )
    .expect("USART3 默认配置有效");
    let (tx2, rx2) = usart2.split();
    let (tx3, rx3) = usart3.split();

    let board = Board {
        motors: Motors {
            tim1,
            tim9,
            tim10,
            tim11,
        },
        encoders: Encoders {
            m1,
            m2,
            m3,
            m4,
            last,
            total: [0; 4],
        },
        led,
        tx2,
        tx3,
        log,
        _buzzer: buzzer,
    };
    (board, rx2, rx3)
}
