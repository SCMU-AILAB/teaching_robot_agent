// firmware/car-core/src/motion.rs
//! 运动状态机：命令转四轮目标转速，处理看门狗、斜率限制与故障锁定；时间由调用方传入.

use crate::kinematics::{WheelMap, mix_mecanum};
use crate::protocol::Command;

/// 运动指令有效期（毫秒），超时自动停车.
pub const WATCHDOG_MS: u32 = 300;
/// 每个 10 ms 控制周期每轮目标转速千分比的最大变化（0 到满速约 0.5 秒）.
pub const SLEW_PER_TICK: i16 = 20;
/// 故障锁定期间拒绝运动命令的应答码.
pub const ACK_FAULT: u8 = 5;

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
    /// 堵转或编码器故障，停止命令解除前拒绝运动.
    Fault = 3,
}

/// 命令目标与斜率限制后的目标转速（电机 M1..M4 顺序，千分比）.
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

    /// 应用一条已校验的命令，返回应答码：0 成功，[`ACK_FAULT`] 故障锁定.
    pub fn apply(&mut self, command: &Command, now_ms: u32) -> u8 {
        let locked = self.run_state == RunState::Fault;
        match *command {
            Command::Velocity { .. } | Command::Motor { .. } if locked => return ACK_FAULT,
            Command::Velocity { vx, vy, wz, .. } => {
                self.target = self.map.to_motors(mix_mecanum(vx, vy, wz));
                self.start_motion(now_ms);
            }
            Command::Motor { motor, duty, .. } => {
                // 单电机测试直接作用于物理电机，不经过轮位映射
                self.target = [0; 4];
                self.target[usize::from(motor - 1)] = duty;
                self.start_motion(now_ms);
            }
            Command::Stop { .. } => self.halt(RunState::Stopped),
            Command::Ping { .. } => {}
        }
        0
    }

    /// 闭环检测到堵转或编码器故障时立即停车并锁定.
    pub fn set_fault(&mut self) {
        self.halt(RunState::Fault);
    }

    /// 推进一个控制周期并返回本周期目标转速.
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

    /// 当前（斜率限制后的）目标转速.
    #[must_use]
    pub const fn output(&self) -> [i16; 4] {
        self.output
    }

    /// 当前运动状态.
    #[must_use]
    pub const fn run_state(&self) -> RunState {
        self.run_state
    }

    fn halt(&mut self, state: RunState) {
        self.target = [0; 4];
        self.output = [0; 4];
        self.last_motion_ms = None;
        self.run_state = state;
    }

    fn start_motion(&mut self, now_ms: u32) {
        self.last_motion_ms = Some(now_ms);
        self.run_state = RunState::Running;
    }
}
