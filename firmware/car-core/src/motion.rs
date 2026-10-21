// firmware/car-core/src/motion.rs
//! 运动状态机：命令转目标占空比，处理看门狗与斜率限制；时间由调用方传入.

use crate::kinematics::{DUTY_LIMIT, WheelMap, mix_mecanum};
use crate::protocol::{Command, PERMILLE_MAX};

/// 运动指令有效期（毫秒），超时自动停车.
pub const WATCHDOG_MS: u32 = 300;
/// 每个 10 ms 控制周期每轮占空千分比的最大变化.
pub const SLEW_PER_TICK: i16 = 20;

/// 遥测中的运动状态.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum RunState {
    /// 未收到运动指令或已收到停止命令.
    Stopped = 0,
    /// 运动指令有效.
    Running = 1,
    /// 运动指令超时后停车.
    WatchdogStopped = 2,
}

/// 目标与实际输出占空比（电机 M1..M4 顺序）.
pub struct MotionState {
    map: WheelMap,
    target: [i16; 4],
    output: [i16; 4],
    last_motion_ms: Option<u32>,
    run_state: RunState,
}

impl MotionState {
    /// 以停车状态创建.
    #[must_use]
    pub const fn new(map: WheelMap) -> Self {
        Self {
            map,
            target: [0; 4],
            output: [0; 4],
            last_motion_ms: None,
            run_state: RunState::Stopped,
        }
    }

    /// 应用一条已校验的命令.
    pub fn apply(&mut self, command: &Command, now_ms: u32) {
        match *command {
            Command::Velocity { vx, vy, wz, .. } => {
                self.target = self.map.to_motors(mix_mecanum(vx, vy, wz));
                self.start_motion(now_ms);
            }
            Command::Motor { motor, duty, .. } => {
                // 单电机测试直接作用于物理电机，不经过轮位映射
                let scaled = i32::from(duty) * i32::from(DUTY_LIMIT) / PERMILLE_MAX;
                self.target = [0; 4];
                self.target[usize::from(motor - 1)] = scaled as i16;
                self.start_motion(now_ms);
            }
            Command::Stop { .. } => {
                self.target = [0; 4];
                self.output = [0; 4];
                self.last_motion_ms = None;
                self.run_state = RunState::Stopped;
            }
            Command::Ping { .. } => {}
        }
    }

    /// 推进一个控制周期并返回本周期输出占空比.
    pub fn tick(&mut self, now_ms: u32) -> [i16; 4] {
        if let Some(last) = self.last_motion_ms
            && now_ms.wrapping_sub(last) > WATCHDOG_MS
        {
            self.target = [0; 4];
            self.last_motion_ms = None;
            self.run_state = RunState::WatchdogStopped;
        }
        for (out, &target) in self.output.iter_mut().zip(self.target.iter()) {
            *out += (target - *out).clamp(-SLEW_PER_TICK, SLEW_PER_TICK);
        }
        self.output
    }

    /// 当前输出占空比.
    #[must_use]
    pub const fn output(&self) -> [i16; 4] {
        self.output
    }

    /// 当前运动状态.
    #[must_use]
    pub const fn run_state(&self) -> RunState {
        self.run_state
    }

    fn start_motion(&mut self, now_ms: u32) {
        self.last_motion_ms = Some(now_ms);
        self.run_state = RunState::Running;
    }
}
