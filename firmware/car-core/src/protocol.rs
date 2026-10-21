// firmware/car-core/src/protocol.rs
//! 文本行协议：`$命令,字段...*CRC\n` 的解析与编码.

use core::fmt::Write as _;

use crate::crc::crc8;

/// 小车接收的命令帧（不含换行）最大字节数.
pub const MAX_COMMAND_LINE: usize = 63;
/// 小车发出帧的最大字节数（含换行）.
pub const MAX_OUTPUT_FRAME: usize = 96;
/// 速度、占空比字段的千分比上限.
pub const PERMILLE_MAX: i32 = 1000;

/// 主机发给小车的命令.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Command {
    /// 归一化速度：前进、向左平移、逆时针旋转，均为千分比.
    Velocity { seq: u16, vx: i16, vy: i16, wz: i16 },
    /// 单电机测试：电机号 1..=4，占空千分比.
    Motor { seq: u16, motor: u8, duty: i16 },
    /// 立即停车.
    Stop { seq: u16 },
    /// 心跳，只回复应答，不刷新运动看门狗.
    Ping { seq: u16 },
}

impl Command {
    /// 返回命令序号.
    #[must_use]
    pub const fn seq(&self) -> u16 {
        match *self {
            Self::Velocity { seq, .. }
            | Self::Motor { seq, .. }
            | Self::Stop { seq }
            | Self::Ping { seq } => seq,
        }
    }
}

/// 无法取得可靠序号时的错误帧原因.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum ErrorReason {
    /// 校验错误.
    Checksum = 1,
    /// 帧超长.
    TooLong = 2,
    /// 格式错误：缺少校验、字段不是整数或序号越界.
    Malformed = 3,
}

/// 可取得序号时的拒绝应答码.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum RejectCode {
    /// 参数越界.
    OutOfRange = 2,
    /// 未知命令.
    UnknownCommand = 3,
    /// 字段数量错误.
    FieldCount = 4,
}

/// 解析失败：整帧不可用，或命令可识别序号但被拒绝.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ParseError {
    /// 回复 `$E,reason`.
    Frame(ErrorReason),
    /// 回复 `$A,seq,code`.
    Reject { seq: u16, code: RejectCode },
}

/// 解析十进制整数字段，只接受可选负号加数字.
fn parse_int(field: &[u8]) -> Option<i64> {
    let (negative, digits) = match field.split_first() {
        Some((b'-', rest)) => (true, rest),
        _ => (false, field),
    };
    // 限制位数，避免溢出；协议字段最长为 i32 的 10 位数字
    if digits.is_empty() || digits.len() > 10 {
        return None;
    }
    let mut value: i64 = 0;
    for &byte in digits {
        if !byte.is_ascii_digit() {
            return None;
        }
        value = value * 10 + i64::from(byte - b'0');
    }
    Some(if negative { -value } else { value })
}

/// 把两位大写或小写十六进制解析为字节.
fn parse_hex_byte(text: &[u8]) -> Option<u8> {
    if text.len() != 2 {
        return None;
    }
    let digit = |byte: u8| (byte as char).to_digit(16);
    Some((digit(text[0])? * 16 + digit(text[1])?) as u8)
}

/// 解析一行命令帧（不含换行与回车）.
///
/// # Errors
///
/// 帧格式、校验或长度错误返回 [`ParseError::Frame`]；
/// 序号可识别但命令、字段数量或参数不合法时返回 [`ParseError::Reject`]。
pub fn parse_frame(line: &[u8]) -> Result<Command, ParseError> {
    use ParseError::{Frame, Reject};

    // ========== Step1: 长度、起始符与校验 ==========
    if line.len() > MAX_COMMAND_LINE {
        return Err(Frame(ErrorReason::TooLong));
    }
    let body = line
        .strip_prefix(b"$")
        .ok_or(Frame(ErrorReason::Malformed))?;
    let star = body
        .iter()
        .rposition(|&byte| byte == b'*')
        .ok_or(Frame(ErrorReason::Malformed))?;
    let (payload, checksum) = (&body[..star], &body[star + 1..]);
    let expected = parse_hex_byte(checksum).ok_or(Frame(ErrorReason::Malformed))?;
    if crc8(payload) != expected {
        return Err(Frame(ErrorReason::Checksum));
    }

    // ========== Step2: 命令名与序号；序号不可靠时只能回复错误帧 ==========
    let mut fields = payload.split(|&byte| byte == b',');
    let name = fields.next().unwrap_or_default();
    let seq = fields
        .next()
        .and_then(parse_int)
        .and_then(|value| u16::try_from(value).ok())
        .ok_or(Frame(ErrorReason::Malformed))?;
    let mut values = [0_i64; 3];
    let mut count = 0;
    for field in fields {
        let value = parse_int(field).ok_or(Frame(ErrorReason::Malformed))?;
        if count < values.len() {
            values[count] = value;
        }
        count += 1;
    }

    // ========== Step3: 按命令校验字段数量与范围 ==========
    let reject = |code| Reject { seq, code };
    let expect = |n: usize| {
        if count == n {
            Ok(())
        } else {
            Err(reject(RejectCode::FieldCount))
        }
    };
    let permille = |value: i64| {
        i16::try_from(value)
            .ok()
            .filter(|v| i32::from(*v).abs() <= PERMILLE_MAX)
            .ok_or(reject(RejectCode::OutOfRange))
    };
    match name {
        b"V" => {
            expect(3)?;
            Ok(Command::Velocity {
                seq,
                vx: permille(values[0])?,
                vy: permille(values[1])?,
                wz: permille(values[2])?,
            })
        }
        b"M" => {
            expect(2)?;
            let motor = u8::try_from(values[0])
                .ok()
                .filter(|m| (1..=4).contains(m))
                .ok_or(reject(RejectCode::OutOfRange))?;
            Ok(Command::Motor {
                seq,
                motor,
                duty: permille(values[1])?,
            })
        }
        b"S" => expect(0).map(|()| Command::Stop { seq }),
        b"P" => expect(0).map(|()| Command::Ping { seq }),
        _ => Err(reject(RejectCode::UnknownCommand)),
    }
}

/// 发送帧的固定容量缓冲区，避免堆分配.
pub struct FrameBuf {
    data: [u8; MAX_OUTPUT_FRAME],
    len: usize,
}

impl Default for FrameBuf {
    fn default() -> Self {
        Self::new()
    }
}

impl FrameBuf {
    /// 创建空缓冲区.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            data: [0; MAX_OUTPUT_FRAME],
            len: 0,
        }
    }

    /// 返回已编码的字节.
    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        &self.data[..self.len]
    }

    /// 返回已编码的文本；内容只由 ASCII 组成.
    #[must_use]
    pub fn as_str(&self) -> &str {
        core::str::from_utf8(self.as_bytes()).unwrap_or_default()
    }
}

impl core::fmt::Write for FrameBuf {
    fn write_str(&mut self, text: &str) -> core::fmt::Result {
        let end = self.len + text.len();
        if end > self.data.len() {
            return Err(core::fmt::Error);
        }
        self.data[self.len..end].copy_from_slice(text.as_bytes());
        self.len = end;
        Ok(())
    }
}

/// 以 `$` 开头写入负载，再追加 `*CRC\n`.
fn encode(buf: &mut FrameBuf, payload: core::fmt::Arguments<'_>) -> core::fmt::Result {
    buf.len = 0;
    buf.write_str("$")?;
    buf.write_fmt(payload)?;
    let crc = crc8(&buf.data[1..buf.len]);
    writeln!(buf, "*{crc:02X}")
}

/// 编码应答帧 `$A,seq,code`.
///
/// # Errors
///
/// 缓冲区不足时返回错误（固定字段长度下不会发生）。
pub fn encode_ack(buf: &mut FrameBuf, seq: u16, code: u8) -> core::fmt::Result {
    encode(buf, format_args!("A,{seq},{code}"))
}

/// 编码错误帧 `$E,reason`.
///
/// # Errors
///
/// 缓冲区不足时返回错误（固定字段长度下不会发生）。
pub fn encode_error(buf: &mut FrameBuf, reason: u8) -> core::fmt::Result {
    encode(buf, format_args!("E,{reason}"))
}

/// 编码遥测帧 `$T,ms,state,e1..e4,d1..d4`.
///
/// # Errors
///
/// 缓冲区不足时返回错误（最坏情况约 88 字节，小于缓冲区）。
pub fn encode_telemetry(
    buf: &mut FrameBuf,
    ms: u32,
    state: u8,
    encoders: [i32; 4],
    duties: [i16; 4],
) -> core::fmt::Result {
    let [e1, e2, e3, e4] = encoders;
    let [d1, d2, d3, d4] = duties;
    encode(
        buf,
        format_args!("T,{ms},{state},{e1},{e2},{e3},{e4},{d1},{d2},{d3},{d4}"),
    )
}

/// 编码开机帧 `$B,version,port`.
///
/// # Errors
///
/// 缓冲区不足时返回错误（固定字段长度下不会发生）。
pub fn encode_boot(buf: &mut FrameBuf, version: u16, port: u8) -> core::fmt::Result {
    encode(buf, format_args!("B,{version},{port}"))
}
