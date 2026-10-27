// firmware/car-core/tests/vectors.rs
//! 读取与 Python 共用的测试向量，校验帧解析与编码.

use car_core::protocol::{
    Command, FrameBuf, ParseError, encode_ack, encode_boot, encode_error, encode_telemetry,
    parse_frame,
};

/// 把解析结果格式化为向量文件中的期望文本.
fn describe(result: Result<Command, ParseError>) -> String {
    match result {
        Ok(Command::Velocity { seq, vx, vy, wz }) => format!("V {seq} {vx} {vy} {wz}"),
        Ok(Command::Motor { seq, motor, duty }) => format!("M {seq} {motor} {duty}"),
        Ok(Command::Stop { seq }) => format!("S {seq}"),
        Ok(Command::Ping { seq }) => format!("P {seq}"),
        Err(ParseError::Reject { seq, code }) => format!("A {seq} {}", code as u8),
        Err(ParseError::Frame(reason)) => format!("E {}", reason as u8),
    }
}

#[test]
fn crc8_matches_reference_check_value() {
    assert_eq!(car_core::crc::crc8(b"123456789"), 0xF4);
}

#[test]
fn parse_vectors_match() {
    for line in include_str!("vectors.txt").lines() {
        if line.starts_with('#') || line.is_empty() {
            continue;
        }
        let (frame, expected) = line.split_once('\t').expect("向量缺少制表符");
        assert_eq!(
            describe(parse_frame(frame.as_bytes())),
            expected,
            "帧 {frame}"
        );
    }
}

#[test]
fn encode_vectors_match() {
    let mut frames = Vec::new();
    let mut buf = FrameBuf::new();
    encode_ack(&mut buf, 5, 0).unwrap();
    frames.push(buf.as_str().to_owned());
    encode_error(&mut buf, 1).unwrap();
    frames.push(buf.as_str().to_owned());
    encode_telemetry(
        &mut buf,
        123_456,
        1,
        [-5, 0, 70_000, i32::MIN],
        [400, -400, 0, 20],
    )
    .unwrap();
    frames.push(buf.as_str().to_owned());
    encode_boot(&mut buf, 1, 2).unwrap();
    frames.push(buf.as_str().to_owned());
    let expected: Vec<String> = include_str!("encode_vectors.txt")
        .lines()
        .filter(|line| !line.starts_with('#') && !line.is_empty())
        .map(|line| format!("{line}\n"))
        .collect();
    assert_eq!(frames, expected);
}

#[test]
fn worst_case_telemetry_fits_buffer() {
    let mut buf = FrameBuf::new();
    encode_telemetry(&mut buf, u32::MAX, 2, [i32::MIN; 4], [-400; 4]).unwrap();
    assert!(buf.as_str().len() <= 96);
}
