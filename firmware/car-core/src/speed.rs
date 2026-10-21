// firmware/car-core/src/speed.rs
//! 四轮转速 PI 闭环：目标转速（千分比）与每周期编码器增量换算为占空比，含堵转与编码器故障检测.

/// 满量程转速对应的每 10 ms 编码器计数（即 4000 计数/秒）.
pub const COUNTS_PER_TICK_FULL: i32 = 40;
/// 闭环输出占空比上限（千分比），为 PI 留出调节余量.
pub const DUTY_MAX: i16 = 800;

/// 前馈：占空比 ≈ 目标转速 × 0.37 + 起转偏置，取自 2026-10-21 慢衰减实测.
const FF_NUM: i32 = 370;
const FF_OFFSET: i32 = 30;
/// 比例与积分增益（分母 1000），前馈承担主要输出，PI 只修正残差.
const KP_NUM: i32 = 150;
const KI_NUM: i32 = 20;
const GAIN_DEN: i32 = 1000;
/// 占空比不低于 60% 而转速低于 5% 持续 1 秒，判定为堵转或编码器失效.
const FAULT_DUTY: i32 = 600;
const FAULT_SPEED: i32 = 50;
const FAULT_TICKS: u16 = 100;

/// 单个轮子的 PI 状态.
#[derive(Clone, Copy, Default)]
struct Wheel {
    /// 积分项，单位为占空千分比 × [`GAIN_DEN`].
    integral: i32,
    stall_ticks: u16,
}

/// 四轮转速闭环；故障后输出恒为零，直到调用 [`SpeedLoop::reset`].
pub struct SpeedLoop {
    wheels: [Wheel; 4],
    fault: bool,
}

impl Default for SpeedLoop {
    fn default() -> Self {
        Self::new()
    }
}

impl SpeedLoop {
    /// 创建零状态闭环.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            wheels: [Wheel {
                integral: 0,
                stall_ticks: 0,
            }; 4],
            fault: false,
        }
    }

    /// 是否已判定堵转或编码器故障.
    #[must_use]
    pub const fn fault(&self) -> bool {
        self.fault
    }

    /// 清除积分与故障状态，由停止命令触发.
    pub fn reset(&mut self) {
        *self = Self::new();
    }

    /// 推进一个 10 ms 周期：输入目标转速千分比与本周期编码器增量，返回占空千分比.
    pub fn update(&mut self, targets: [i16; 4], deltas: [i32; 4]) -> [i16; 4] {
        let mut duties = [0_i16; 4];
        for (i, wheel) in self.wheels.iter_mut().enumerate() {
            let target = i32::from(targets[i]);
            if target == 0 {
                // 目标为零时滑行停车并清空积分，避免静止时积分缓慢爬升
                *wheel = Wheel::default();
                continue;
            }
            let measured = deltas[i].saturating_mul(1000 / COUNTS_PER_TICK_FULL);
            let error = target - measured;
            let feedforward = target * FF_NUM / GAIN_DEN + target.signum() * FF_OFFSET;
            let limit = i32::from(DUTY_MAX);
            let integral =
                (wheel.integral + error * KI_NUM).clamp(-limit * GAIN_DEN, limit * GAIN_DEN);
            let raw = feedforward + error * KP_NUM / GAIN_DEN + integral / GAIN_DEN;
            let duty = raw.clamp(-limit, limit);
            // 抗积分饱和：输出已饱和且误差继续推向饱和方向时不累积积分
            if duty == raw || error.signum() != raw.signum() {
                wheel.integral = integral;
            }
            wheel.stall_ticks = if duty.abs() >= FAULT_DUTY && measured.abs() < FAULT_SPEED {
                wheel.stall_ticks.saturating_add(1)
            } else {
                0
            };
            if wheel.stall_ticks >= FAULT_TICKS {
                self.fault = true;
            }
            // duty 已限制在 ±DUTY_MAX，转换不会截断
            duties[i] = duty as i16;
        }
        if self.fault { [0; 4] } else { duties }
    }
}
