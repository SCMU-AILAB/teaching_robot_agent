// firmware/car-core/src/crc.rs
//! CRC-8（多项式 0x07，初值 0，不反射，无输出异或）.

/// 计算字节序列的 CRC-8.
#[must_use]
pub fn crc8(data: &[u8]) -> u8 {
    let mut crc: u8 = 0;
    for &byte in data {
        crc ^= byte;
        for _ in 0..8 {
            crc = if crc & 0x80 != 0 {
                (crc << 1) ^ 0x07
            } else {
                crc << 1
            };
        }
    }
    crc
}
