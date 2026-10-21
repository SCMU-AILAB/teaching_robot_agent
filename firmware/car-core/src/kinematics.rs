// firmware/car-core/src/kinematics.rs
//! 麦克纳姆轮归一化运动学与轮位到电机的映射.

use crate::protocol::PERMILLE_MAX;

/// 阶段 1 占空比上限（千分比），即最大 40% 占空比.
pub const DUTY_LIMIT: i16 = 400;

/// 把归一化速度换算为四轮占空千分比，顺序为左前、右前、左后、右后.
///
/// 任一轮超过 ±1000 时四轮等比缩放以保持方向，再乘以 [`DUTY_LIMIT`]。
#[must_use]
pub fn mix_mecanum(vx: i16, vy: i16, wz: i16) -> [i16; 4] {
    let (vx, vy, wz) = (i32::from(vx), i32::from(vy), i32::from(wz));
    let wheels = [vx - vy - wz, vx + vy + wz, vx + vy - wz, vx - vy + wz];
    let peak = wheels.iter().map(|w| w.abs()).max().unwrap_or(0);
    let scale = peak.max(PERMILLE_MAX);
    // 结果绝对值不超过 DUTY_LIMIT，转换不会截断
    wheels.map(|w| (w * i32::from(DUTY_LIMIT) / scale) as i16)
}

/// 轮位（左前、右前、左后、右后）到电机序号（0..=3）与方向符号的映射.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WheelMap {
    motor_of_wheel: [usize; 4],
    sign_of_wheel: [i8; 4],
}

impl WheelMap {
    /// 轮位与电机一一对应、方向不翻转，用于标定前和测试.
    pub const IDENTITY: Self = Self::new([0, 1, 2, 3], [1, 1, 1, 1]);

    /// 创建映射；`motor_of_wheel` 必须是 0..=3 的一个排列，`sign_of_wheel` 取 ±1.
    #[must_use]
    pub const fn new(motor_of_wheel: [usize; 4], sign_of_wheel: [i8; 4]) -> Self {
        Self {
            motor_of_wheel,
            sign_of_wheel,
        }
    }

    /// 把四轮占空比换算为电机 M1..M4 的占空比.
    #[must_use]
    pub fn to_motors(&self, wheels: [i16; 4]) -> [i16; 4] {
        let mut motors = [0; 4];
        for (wheel, &duty) in wheels.iter().enumerate() {
            motors[self.motor_of_wheel[wheel]] = duty * i16::from(self.sign_of_wheel[wheel]);
        }
        motors
    }
}
