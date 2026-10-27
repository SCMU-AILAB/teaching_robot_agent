// firmware/car-core/tests/speed.rs
//! 轮速 PI 闭环：用一阶电机模型验证收敛、零目标与故障检测.

use car_core::speed::{COUNTS_PER_TICK_FULL, DUTY_MAX, SpeedLoop};

/// 一阶电机模型：每周期计数向 gain×(占空比−死区) 逼近，单位与固件一致.
struct Motor {
    gain: f32,
    speed: f32,
}

impl Motor {
    fn step(&mut self, duty: i16) -> i32 {
        let effective = (f32::from(duty.unsigned_abs()) - 30.0).max(0.0) * f32::from(duty.signum());
        let steady = effective * self.gain;
        self.speed += (steady - self.speed) * 0.3;
        self.speed.round() as i32
    }
}

/// 以四个不同增益的电机运行若干周期，返回最后的测得计数与占空比.
fn simulate(gains: [f32; 4], targets: [i16; 4], ticks: usize) -> ([i32; 4], [i16; 4], SpeedLoop) {
    let mut motors = gains.map(|gain| Motor { gain, speed: 0.0 });
    let mut speed_loop = SpeedLoop::new();
    let mut deltas = [0; 4];
    let mut duties = [0; 4];
    for _ in 0..ticks {
        duties = speed_loop.update(targets, deltas);
        for i in 0..4 {
            deltas[i] = motors[i].step(duties[i]);
        }
    }
    (deltas, duties, speed_loop)
}

#[test]
fn mismatched_motors_converge_to_same_speed() {
    // 增益相差约 1.5 倍，模拟实测中电机快慢与正反转差异
    let gains = [0.11, 0.13, 0.09, 0.12];
    let targets = [500, -500, 500, -500];
    let (deltas, _, speed_loop) = simulate(gains, targets, 150);
    let expected = COUNTS_PER_TICK_FULL / 2;
    for (i, delta) in deltas.iter().enumerate() {
        let want = expected * i32::from(targets[i].signum());
        assert!((delta - want).abs() <= 1, "电机 {i}: {delta} 期望 {want}");
    }
    assert!(!speed_loop.fault());
}

#[test]
fn zero_target_outputs_zero_duty() {
    let (_, duties, _) = simulate([0.1; 4], [0; 4], 20);
    assert_eq!(duties, [0; 4]);
}

#[test]
fn duty_never_exceeds_limit() {
    // 增益过小的电机无法达到目标，输出应饱和在上限而非溢出
    let (_, duties, _) = simulate([0.02; 4], [1000, -1000, 1000, -1000], 30);
    for duty in duties {
        assert_eq!(duty.unsigned_abs(), DUTY_MAX.unsigned_abs());
    }
}

#[test]
fn dead_encoder_triggers_fault_and_reset_clears_it() {
    let mut speed_loop = SpeedLoop::new();
    let mut tripped_at = None;
    for tick in 0..200 {
        let _ = speed_loop.update([800, 0, 0, 0], [0; 4]);
        if speed_loop.fault() {
            tripped_at = Some(tick);
            break;
        }
    }
    let tick = tripped_at.expect("编码器无反馈时应判定故障");
    assert!((90..=150).contains(&tick), "故障判定时刻 {tick}");
    assert_eq!(speed_loop.update([800, 0, 0, 0], [0; 4]), [0; 4]);
    speed_loop.reset();
    assert!(!speed_loop.fault());
}
