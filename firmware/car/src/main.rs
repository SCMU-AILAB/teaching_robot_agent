// firmware/car/src/main.rs
//! 开发链路验证程序：两个候选 LED 引脚以不同频率闪烁，三个串口轮流打印问候.
#![no_std]
#![no_main]

use core::fmt::Write as _;

use embassy_executor::Spawner;
use embassy_stm32::gpio::{Level, Output, Speed};
use embassy_stm32::usart::{Config as UartConfig, UartTx};
use embassy_time::Timer;
use panic_halt as _;

/// 固定容量的格式化缓冲区，避免引入堆分配.
struct LineBuf {
    data: [u8; 64],
    len: usize,
}

impl LineBuf {
    const fn new() -> Self {
        Self {
            data: [0; 64],
            len: 0,
        }
    }

    fn as_bytes(&self) -> &[u8] {
        &self.data[..self.len]
    }
}

impl core::fmt::Write for LineBuf {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        let bytes = s.as_bytes();
        let end = self.len + bytes.len();
        if end > self.data.len() {
            return Err(core::fmt::Error);
        }
        self.data[self.len..end].copy_from_slice(bytes);
        self.len = end;
        Ok(())
    }
}

#[embassy_executor::main]
async fn main(_spawner: Spawner) {
    // 默认使用内部 16 MHz 时钟；外部晶振频率确认前不改时钟树
    let p = embassy_stm32::init(Default::default());

    // ========== Step1: 原固件中初始化为推挽输出的两个引脚，其一应为 LED ==========
    let mut pe10 = Output::new(p.PE10, Level::Low, Speed::Low);
    let mut pa8 = Output::new(p.PA8, Level::Low, Speed::Low);

    // ========== Step2: 三个串口只发不收，参数与原固件一致：115200 8N1 ==========
    let config = UartConfig::default();
    let mut usart1 = UartTx::new_blocking(p.USART1, p.PA9, config).unwrap();
    let mut usart2 = UartTx::new_blocking(p.USART2, p.PD5, config).unwrap();
    let mut usart3 = UartTx::new_blocking(p.USART3, p.PD8, config).unwrap();

    let mut tick: u32 = 0;
    loop {
        // PA8 每 250 ms 翻转（2 Hz 闪烁），PE10 每 500 ms 翻转（1 Hz 闪烁），便于肉眼区分
        pa8.toggle();
        if tick.is_multiple_of(2) {
            pe10.toggle();
        }
        if tick.is_multiple_of(4) {
            let seconds = tick / 4;
            for (name, port) in [
                ("USART1", &mut usart1),
                ("USART2", &mut usart2),
                ("USART3", &mut usart3),
            ] {
                let mut line = LineBuf::new();
                // 单行长度远小于缓冲区，格式化失败时跳过本次输出
                if write!(line, "hello from {name} t={seconds}s\r\n").is_ok() {
                    let _ = port.blocking_write(line.as_bytes());
                }
            }
        }
        tick = tick.wrapping_add(1);
        Timer::after_millis(250).await;
    }
}
