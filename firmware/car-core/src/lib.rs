// firmware/car-core/src/lib.rs
//! 小车底盘纯逻辑：串口协议、麦克纳姆运动学与运动状态机，不依赖硬件.
#![no_std]

pub mod crc;
pub mod kinematics;
pub mod line;
pub mod motion;
pub mod protocol;
