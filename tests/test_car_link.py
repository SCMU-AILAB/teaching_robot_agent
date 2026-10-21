# tests/test_car_link.py
"""小车串口协议的编码、解析与串口封装测试，与 Rust 固件共用测试向量."""

import unittest
from pathlib import Path

from app.car_teleop import DriveState, apply_key
from robot.car_link import (
    Ack,
    Boot,
    CarLink,
    ErrorFrame,
    Telemetry,
    compensate_strafe,
    crc8,
    motor,
    open_serial,
    parse_frame,
    ping,
    stop,
    velocity,
)

VECTORS = Path(__file__).resolve().parents[1] / "firmware/car-core/tests"


def vector_lines(name: str) -> list[str]:
    """读取向量文件中的有效行."""
    lines = (VECTORS / name).read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line and not line.startswith("#")]


def encode_expected(expected: str) -> bytes:
    """按向量期望文本调用对应编码函数."""
    name, *fields = expected.split()
    values = [int(field) for field in fields]
    match name:
        case "V":
            return velocity(values[0], values[1], values[2], values[3])
        case "M":
            return motor(values[0], values[1], values[2])
        case "S":
            return stop(values[0])
        case _:
            return ping(values[0])


class FakePort:
    """记录写入并按预设分块返回读取数据的串口替身."""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        """预设每次 read 返回的数据块."""
        self.chunks: list[bytes] = list(chunks or [])
        self.written: list[bytes] = []
        self.closed: bool = False

    def read(self, size: int = 1) -> bytes:
        """返回下一块数据，没有数据时返回空字节."""
        del size
        return self.chunks.pop(0) if self.chunks else b""

    def write(self, data: bytes, /) -> int | None:
        """记录写入内容."""
        self.written.append(data)
        return len(data)

    def close(self) -> None:
        """记录关闭."""
        self.closed = True


class FakeSerial(FakePort):
    """记录控制线设置顺序的 pyserial 替身."""

    def __init__(self) -> None:
        """初始化为未打开状态，pyserial 默认 DTR/RTS 为高."""
        super().__init__()
        self.port: str | None = None
        self.baudrate: int = 9600
        self.timeout: float | None = None
        self._dtr: bool = True
        self._rts: bool = True
        self.lines_at_open: tuple[bool, bool] | None = None

    @property
    def dtr(self) -> bool:
        """DTR 电平."""
        return self._dtr

    @dtr.setter
    def dtr(self, value: bool) -> None:
        self._dtr = value

    @property
    def rts(self) -> bool:
        """RTS 电平."""
        return self._rts

    @rts.setter
    def rts(self, value: bool) -> None:
        self._rts = value

    def open(self) -> None:
        """记录打开瞬间的控制线状态."""
        self.lines_at_open = (self._dtr, self._rts)


class SharedVectorTest(unittest.TestCase):
    """与 firmware/car-core 共用的向量."""

    def test_crc_reference_value(self) -> None:
        """CRC-8 标准校验值."""
        self.assertEqual(crc8(b"123456789"), 0xF4)

    def test_valid_commands_encode_to_vector_frames(self) -> None:
        """固件可接受的命令帧与 Python 编码逐字节一致."""
        for line in vector_lines("vectors.txt"):
            frame, expected = line.split("\t")
            if expected[0] in "VMSP":
                with self.subTest(frame=frame):
                    self.assertEqual(encode_expected(expected), frame.encode() + b"\n")

    def test_out_of_range_commands_are_refused_before_sending(self) -> None:
        """固件会以越界拒绝的命令，Python 编码阶段即报错."""
        with self.assertRaises(ValueError):
            _ = velocity(9, 1001, 0, 0)
        for index in (0, 5):
            with self.assertRaises(ValueError):
                _ = motor(10, index, 100)
        with self.assertRaises(ValueError):
            _ = stop(70000)
        with self.assertRaises(ValueError):
            _ = velocity(1, True, 0, 0)

    def test_firmware_output_vectors_parse(self) -> None:
        """固件编码的应答、错误、遥测与开机帧均能解析."""
        frames = [
            parse_frame(line.encode()) for line in vector_lines("encode_vectors.txt")
        ]
        self.assertEqual(
            frames,
            [
                Ack(5, 0),
                ErrorFrame(1),
                Telemetry(123456, 1, (-5, 0, 70000, -2147483648), (400, -400, 0, 20)),
                Boot(1, 2),
            ],
        )

    def test_parse_rejects_bad_frames(self) -> None:
        """校验错误、缺少校验、字段数量不符与非整数字段均拒绝."""
        for line in (
            b"$A,5,0*00",
            b"$A,5,0",
            b"$A,5*" + f"{crc8(b'A,5'):02X}".encode(),
            b"$A,x,0*" + f"{crc8(b'A,x,0'):02X}".encode(),
            b"A,5,0*6F",
        ):
            with self.subTest(line=line), self.assertRaises(ValueError):
                _ = parse_frame(line)


class CarLinkTest(unittest.TestCase):
    """串口封装：分块读取、噪声重同步与关闭停车."""

    def make_link(self) -> tuple[FakePort, CarLink]:
        """准备分块到达且夹杂噪声的数据."""
        telemetry = b"$T,123456,1,-5,0,70000,-2147483648,400,-400,0,20*40\r\n"
        port = FakePort(
            [b"noise$A,5,", b"0*6F\n$A,1,0*00\n", telemetry[:20], telemetry[20:]]
        )
        return port, CarLink(port)

    def test_poll_assembles_chunks_and_counts_invalid_lines(self) -> None:
        """跨块帧可拼接，校验错误的行只计数."""
        _, link = self.make_link()
        frames = [frame for _ in range(4) for frame in link.poll()]
        self.assertEqual(frames[0], Ack(5, 0))
        self.assertIsInstance(frames[1], Telemetry)
        self.assertEqual(len(frames), 2)
        self.assertEqual(link.invalid_lines, 1)

    def test_sequence_wraps(self) -> None:
        """序号 65535 之后回到 0."""
        _, link = self.make_link()
        for _ in range(0xFFFE):
            _ = link.next_seq()
        self.assertEqual([link.next_seq() for _ in range(3)], [0xFFFF, 0, 1])

    def test_close_sends_stop_then_closes(self) -> None:
        """关闭前发送停车命令."""
        port, link = self.make_link()
        link.close()
        self.assertTrue(port.written[-1].startswith(b"$S,"))
        self.assertTrue(port.closed)

    def test_open_serial_lowers_control_lines_before_open(self) -> None:
        """打开串口前 DTR、RTS 已置低，避免复位单片机."""
        fake = FakeSerial()
        port = open_serial("/dev/null", _factory=lambda: fake)
        self.assertIs(port, fake)
        self.assertEqual(fake.lines_at_open, (False, False))
        self.assertEqual(fake.baudrate, 115200)


class StrafeTrimTest(unittest.TestCase):
    """横移补偿：按横移速度叠加反向前后速度."""

    def test_trim_adds_proportional_forward_component(self) -> None:
        """补偿量为 vy × trim / 1000，与横移方向同号或反号取决于 trim."""
        self.assertEqual(compensate_strafe(0, 500, -120), (-60, 500))
        self.assertEqual(compensate_strafe(0, -500, -120), (60, -500))
        self.assertEqual(compensate_strafe(300, 0, -120), (300, 0))

    def test_trim_result_is_clamped(self) -> None:
        """叠加后前后速度不超出千分比范围."""
        self.assertEqual(compensate_strafe(950, 1000, 200), (1000, 1000))
        self.assertEqual(compensate_strafe(-950, 1000, -200), (-1000, 1000))

    def test_trim_range_is_validated(self) -> None:
        """补偿系数只允许 -500..500."""
        with self.assertRaises(ValueError):
            _ = compensate_strafe(0, 100, 600)


class TeleopKeyTest(unittest.TestCase):
    """遥控按键：切换保持、调速与退出."""

    def test_keys_latch_single_axis_motion(self) -> None:
        """方向键设置单一轴运动，空格停车，X 退出."""
        state = DriveState(speed=500)
        self.assertEqual(apply_key(state, "w"), DriveState(500, 0, 0, 500))
        self.assertEqual(apply_key(state, "A"), DriveState(0, 500, 0, 500))
        self.assertEqual(apply_key(state, "e"), DriveState(0, 0, -500, 500))
        moving = DriveState(500, 0, 0, 500)
        self.assertEqual(apply_key(moving, " "), DriveState(0, 0, 0, 500))
        self.assertIsNone(apply_key(state, "x"))
        self.assertEqual(apply_key(state, "z"), state)

    def test_bracket_keys_adjust_trim_and_keep_motion(self) -> None:
        """[ 与 ] 以 10 为步长调整补偿系数，并保持当前运动."""
        moving = DriveState(0, 500, 0, 500, trim=0)
        self.assertEqual(apply_key(moving, "["), DriveState(0, 500, 0, 500, trim=-10))
        self.assertEqual(apply_key(moving, "]"), DriveState(0, 500, 0, 500, trim=10))
        edge = apply_key(DriveState(trim=-500), "[")
        self.assertIsNotNone(edge)
        assert edge is not None
        self.assertEqual(edge.trim, -500)

    def test_speed_change_rescales_current_motion(self) -> None:
        """调速同步改变当前方向的速度，并受上下限约束."""
        moving = DriveState(0, -500, 0, 500)
        self.assertEqual(apply_key(moving, "+"), DriveState(0, -600, 0, 600))
        self.assertEqual(apply_key(DriveState(speed=1000), "+"), DriveState(speed=1000))
        self.assertEqual(apply_key(DriveState(speed=100), "-"), DriveState(speed=100))


if __name__ == "__main__":
    _ = unittest.main()
