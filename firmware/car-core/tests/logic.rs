// firmware/car-core/tests/logic.rs
//! 行组装、麦克纳姆运动学与运动状态机测试.

use car_core::kinematics::{DUTY_LIMIT, WheelMap, mix_mecanum};
use car_core::line::{LineAssembler, LineEvent};
use car_core::motion::{MotionState, RunState, SLEW_PER_TICK, WATCHDOG_MS};
use car_core::protocol::Command;

/// 逐字节喂入，收集完整行与超长事件.
fn feed(assembler: &mut LineAssembler, bytes: &[u8]) -> Vec<Result<Vec<u8>, ()>> {
    let mut events = Vec::new();
    for &byte in bytes {
        match assembler.push(byte) {
            Some(LineEvent::Line(line)) => events.push(Ok(line.to_vec())),
            Some(LineEvent::Overflow) => events.push(Err(())),
            None => {}
        }
    }
    events
}

#[test]
fn line_assembler_resyncs_on_dollar_and_ignores_cr() {
    let mut assembler = LineAssembler::new();
    let events = feed(&mut assembler, b"noise$V,1\r\n$S,2*00\n");
    assert_eq!(events, vec![Ok(b"$V,1".to_vec()), Ok(b"$S,2*00".to_vec())]);
    // 半截帧遇到新的 $ 时丢弃旧内容
    let events = feed(&mut assembler, b"$V,9,1$P,3*00\n");
    assert_eq!(events, vec![Ok(b"$P,3*00".to_vec())]);
}

#[test]
fn line_assembler_reports_overflow_once_then_waits_for_dollar() {
    let mut assembler = LineAssembler::new();
    let mut long = vec![b'$'];
    long.extend(std::iter::repeat_n(b'1', 80));
    long.extend(b"\n$P,1*00\n");
    let events = feed(&mut assembler, &long);
    assert_eq!(events, vec![Err(()), Ok(b"$P,1*00".to_vec())]);
}

#[test]
fn mecanum_pure_motions_follow_sign_pattern() {
    let l = DUTY_LIMIT;
    // 顺序：左前、右前、左后、右后
    assert_eq!(mix_mecanum(1000, 0, 0), [l, l, l, l]);
    assert_eq!(mix_mecanum(0, 1000, 0), [-l, l, l, -l]);
    assert_eq!(mix_mecanum(0, 0, 1000), [-l, l, -l, l]);
    assert_eq!(mix_mecanum(-500, 0, 0), [-l / 2, -l / 2, -l / 2, -l / 2]);
}

#[test]
fn mecanum_scales_uniformly_when_saturated() {
    // 左前=0，右前=2000，左后=0，右后=0 缩放至 1000 后再乘上限
    let wheels = mix_mecanum(1000, 1000, 0);
    assert_eq!(wheels, [0, DUTY_LIMIT, DUTY_LIMIT, 0]);
    let wheels = mix_mecanum(1000, 500, 500);
    // 原始：0、2000、1000、1000，缩放比例 1/2
    assert_eq!(wheels, [0, DUTY_LIMIT, DUTY_LIMIT / 2, DUTY_LIMIT / 2]);
}

#[test]
fn wheel_map_routes_and_flips_motors() {
    // 左前→M2 反向，右前→M1，左后→M4，右后→M3 反向
    let map = WheelMap::new([1, 0, 3, 2], [-1, 1, 1, -1]);
    assert_eq!(map.to_motors([10, 20, 30, 40]), [20, -10, -40, 30]);
}

/// 运行若干个 10 ms 控制周期，返回最后一次输出.
fn run(state: &mut MotionState, start_ms: u32, ticks: u32) -> [i16; 4] {
    let mut out = [0; 4];
    for i in 0..ticks {
        out = state.tick(start_ms.wrapping_add(i * 10));
    }
    out
}

#[test]
fn velocity_ramps_up_with_slew_limit() {
    let mut state = MotionState::new(WheelMap::IDENTITY);
    assert_eq!(state.run_state(), RunState::Stopped);
    state.apply(
        &Command::Velocity {
            seq: 1,
            vx: 1000,
            vy: 0,
            wz: 0,
        },
        0,
    );
    assert_eq!(state.tick(0), [SLEW_PER_TICK; 4]);
    assert_eq!(run(&mut state, 10, 30), [DUTY_LIMIT; 4]);
    assert_eq!(state.run_state(), RunState::Running);
}

#[test]
fn watchdog_stops_and_ping_does_not_refresh() {
    let mut state = MotionState::new(WheelMap::IDENTITY);
    state.apply(
        &Command::Velocity {
            seq: 1,
            vx: 1000,
            vy: 0,
            wz: 0,
        },
        0,
    );
    let _ = run(&mut state, 0, 25);
    state.apply(&Command::Ping { seq: 2 }, 250);
    let _ = state.tick(WATCHDOG_MS + 10);
    assert_eq!(state.run_state(), RunState::WatchdogStopped);
    // 看门狗触发后按斜率减速，最终为零
    assert_eq!(run(&mut state, WATCHDOG_MS + 20, 40), [0; 4]);
    // 新的运动指令解除看门狗状态
    state.apply(
        &Command::Velocity {
            seq: 3,
            vx: 0,
            vy: 0,
            wz: 1000,
        },
        800,
    );
    assert_eq!(state.run_state(), RunState::Running);
}

#[test]
fn stop_zeroes_output_immediately() {
    let mut state = MotionState::new(WheelMap::IDENTITY);
    state.apply(
        &Command::Velocity {
            seq: 1,
            vx: 1000,
            vy: 0,
            wz: 0,
        },
        0,
    );
    let _ = run(&mut state, 0, 30);
    state.apply(&Command::Stop { seq: 2 }, 300);
    assert_eq!(state.output(), [0; 4]);
    assert_eq!(state.tick(310), [0; 4]);
    assert_eq!(state.run_state(), RunState::Stopped);
}

#[test]
fn motor_command_drives_single_motor_with_limit() {
    let mut state = MotionState::new(WheelMap::IDENTITY);
    state.apply(
        &Command::Motor {
            seq: 1,
            motor: 3,
            duty: -1000,
        },
        0,
    );
    assert_eq!(run(&mut state, 0, 30), [0, 0, -DUTY_LIMIT, 0]);
}

#[test]
fn watchdog_handles_clock_wraparound() {
    let mut state = MotionState::new(WheelMap::IDENTITY);
    let start = u32::MAX - 50;
    state.apply(
        &Command::Velocity {
            seq: 1,
            vx: 500,
            vy: 0,
            wz: 0,
        },
        start,
    );
    let _ = state.tick(start.wrapping_add(100));
    assert_eq!(state.run_state(), RunState::Running);
    let _ = state.tick(start.wrapping_add(WATCHDOG_MS + 1));
    assert_eq!(state.run_state(), RunState::WatchdogStopped);
}
