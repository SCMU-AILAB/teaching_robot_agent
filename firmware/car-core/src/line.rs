// firmware/car-core/src/line.rs
//! 按字节组装命令行：遇 `$` 重新同步，忽略回车，超长时报告一次并等待下一个 `$`.

use crate::protocol::MAX_COMMAND_LINE;

/// 一次字节输入产生的事件.
#[derive(Debug, PartialEq, Eq)]
pub enum LineEvent<'a> {
    /// 一行完整命令（以 `$` 开头，不含换行）.
    Line(&'a [u8]),
    /// 当前行超出长度上限，已丢弃.
    Overflow,
}

/// 固定容量的行组装器.
pub struct LineAssembler {
    buf: [u8; MAX_COMMAND_LINE],
    len: usize,
    in_line: bool,
}

impl Default for LineAssembler {
    fn default() -> Self {
        Self::new()
    }
}

impl LineAssembler {
    /// 创建空组装器，等待第一个 `$`.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            buf: [0; MAX_COMMAND_LINE],
            len: 0,
            in_line: false,
        }
    }

    /// 输入一个字节，完成一行或发生超长时返回事件.
    pub fn push(&mut self, byte: u8) -> Option<LineEvent<'_>> {
        if byte == b'$' {
            // 新起始符总是开始新行，丢弃未完成的旧内容
            self.buf[0] = byte;
            self.len = 1;
            self.in_line = true;
            return None;
        }
        if !self.in_line || byte == b'\r' {
            return None;
        }
        if byte == b'\n' {
            self.in_line = false;
            return Some(LineEvent::Line(&self.buf[..self.len]));
        }
        if self.len == self.buf.len() {
            self.in_line = false;
            return Some(LineEvent::Overflow);
        }
        self.buf[self.len] = byte;
        self.len += 1;
        None
    }
}
