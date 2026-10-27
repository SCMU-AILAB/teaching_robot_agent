# robot/car_link.py
"""麦克纳姆底盘串口协议：CRC-8 帧编码、回传帧解析与串口收发.

协议定义见 docs/superpowers/specs/2026-10-21-car-base-phase1-design.md。
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

BAUDRATE = 115200
PERMILLE_MAX = 1000
MAX_INPUT_FRAME = 128
COMMAND_NAMES = frozenset({"V", "M", "S", "P"})


def crc8(data: bytes) -> int:
    """计算 CRC-8（多项式 0x07，初值 0，不反射，无输出异或）."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _check_int(value: int, low: int, high: int, name: str) -> int:
    """校验整数字段范围，拒绝布尔值."""
    if isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def encode_command(name: str, seq: int, *fields: int) -> bytes:
    """编码一条主机命令帧，返回含换行的字节.

    Args:
        name: 命令名，只允许 V、M、S、P。
        seq: 0..65535 的序号。
        fields: 命令字段，均为整数。

    Returns:
        以换行结尾的帧，例如 ``$V,1,500,0,0*C8``。

    Raises:
        ValueError: 命令名或序号不合法。
    """
    if name not in COMMAND_NAMES:
        raise ValueError(f"Unknown command {name!r}")
    _ = _check_int(seq, 0, 0xFFFF, "seq")
    payload = ",".join([name, str(seq), *(str(int(f)) for f in fields)]).encode()
    return b"$" + payload + f"*{crc8(payload):02X}\n".encode()


def velocity(seq: int, vx: int, vy: int, wz: int) -> bytes:
    """编码速度命令：前进、向左平移、逆时针旋转，均为千分比."""
    for value, name in ((vx, "vx"), (vy, "vy"), (wz, "wz")):
        _ = _check_int(value, -PERMILLE_MAX, PERMILLE_MAX, name)
    return encode_command("V", seq, vx, vy, wz)


def motor(seq: int, index: int, duty: int) -> bytes:
    """编码单电机测试命令：电机号 1..4，占空千分比."""
    _ = _check_int(index, 1, 4, "motor")
    _ = _check_int(duty, -PERMILLE_MAX, PERMILLE_MAX, "duty")
    return encode_command("M", seq, index, duty)


TRIM_MAX = 500


def compensate_strafe(vx: int, vy: int, trim: int) -> tuple[int, int]:
    """横移时叠加 vy × trim / 1000 的前后速度，抵消对角线抓地力不均造成的前后偏移.

    Args:
        vx: 前进速度千分比。
        vy: 向左平移速度千分比。
        trim: 补偿系数千分比，-500..500；左移往前偏时取负值。

    Returns:
        补偿后的 (vx, vy)，vx 限制在 ±1000。

    Raises:
        ValueError: 补偿系数超出范围。
    """
    _ = _check_int(trim, -TRIM_MAX, TRIM_MAX, "trim")
    product = vy * trim
    # 向零取整，使左右横移的补偿量对称
    adjust = product // PERMILLE_MAX if product >= 0 else -(-product // PERMILLE_MAX)
    return max(-PERMILLE_MAX, min(PERMILLE_MAX, vx + adjust)), vy


def stop(seq: int) -> bytes:
    """编码立即停车命令."""
    return encode_command("S", seq)


def ping(seq: int) -> bytes:
    """编码心跳命令；心跳不刷新小车的运动看门狗."""
    return encode_command("P", seq)


@dataclass(frozen=True)
class Ack:
    """命令应答；code 0 成功，2 越界，3 未知命令，4 字段数量错误，5 故障锁定."""

    seq: int
    code: int


@dataclass(frozen=True)
class ErrorFrame:
    """无法取得序号时的错误：1 校验错误，2 超长，3 格式错误."""

    reason: int


@dataclass(frozen=True)
class Telemetry:
    """20 Hz 遥测：开机毫秒、运动状态、编码器累计计数、实际占空千分比.

    state：0 停止，1 运动，2 看门狗停车，3 堵转或编码器故障（发送停止解除）。
    """

    ms: int
    state: int
    encoders: tuple[int, int, int, int]
    duties: tuple[int, int, int, int]


@dataclass(frozen=True)
class Boot:
    """开机帧：固件版本与发送端口号."""

    version: int
    port: int


CarFrame = Ack | ErrorFrame | Telemetry | Boot
_FIELD_COUNTS = {"A": 2, "E": 1, "T": 10, "B": 2}


def parse_frame(line: bytes) -> CarFrame:
    """解析小车发出的一行（不含换行）.

    Raises:
        ValueError: 长度、格式、校验或字段数量不合法。
    """
    if len(line) > MAX_INPUT_FRAME or not line.startswith(b"$"):
        raise ValueError("Frame must start with '$' and fit the length limit")
    payload, star, checksum = line[1:].rpartition(b"*")
    if not star or len(checksum) != 2:
        raise ValueError("Frame has no two-digit checksum")
    try:
        expected = int(checksum.decode("ascii"), 16)
        parts = payload.decode("ascii").split(",")
        name, values = parts[0], [int(part) for part in parts[1:]]
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("Frame contains non-integer fields") from error
    if crc8(payload) != expected:
        raise ValueError("Frame checksum mismatch")
    if _FIELD_COUNTS.get(name) != len(values):
        raise ValueError(f"Unexpected frame {name!r} with {len(values)} fields")
    match name:
        case "A":
            return Ack(values[0], values[1])
        case "E":
            return ErrorFrame(values[0])
        case "T":
            e1, e2, e3, e4, d1, d2, d3, d4 = values[2:]
            return Telemetry(values[0], values[1], (e1, e2, e3, e4), (d1, d2, d3, d4))
        case _:
            return Boot(values[0], values[1])


class SerialPort(Protocol):
    """CarLink 依赖的最小串口接口，便于测试替换."""

    def read(self, size: int = 1) -> bytes:
        """读取至多 size 字节，超时返回已读到的部分."""
        ...

    def write(self, data: bytes, /) -> int | None:
        """写入全部字节."""
        ...

    def close(self) -> None:
        """关闭串口."""
        ...


class SerialLike(SerialPort, Protocol):
    """可在打开前设置参数和控制线的串口对象，对应 pyserial 的 Serial."""

    @property
    def port(self) -> str | None:
        """串口设备路径."""
        ...

    @port.setter
    def port(self, value: str | None, /) -> None: ...

    @property
    def baudrate(self) -> int:
        """波特率."""
        ...

    @baudrate.setter
    def baudrate(self, value: int, /) -> None: ...

    @property
    def timeout(self) -> float | None:
        """读取超时（秒）."""
        ...

    @timeout.setter
    def timeout(self, value: float | None, /) -> None: ...

    @property
    def dtr(self) -> bool:
        """DTR 控制线."""
        ...

    @dtr.setter
    def dtr(self, value: bool, /) -> None: ...

    @property
    def rts(self) -> bool:
        """RTS 控制线."""
        ...

    @rts.setter
    def rts(self, value: bool, /) -> None: ...

    def open(self) -> None:
        """打开串口."""
        ...


def _default_serial() -> SerialLike:
    """延迟导入 pyserial，只有实际打开串口时才需要 car 依赖组."""
    import serial

    return serial.Serial()


def open_serial(
    path: str,
    *,
    timeout_s: float = 0.02,
    _factory: Callable[[], SerialLike] = _default_serial,
) -> SerialLike:
    """打开小车串口；打开前将 DTR、RTS 置低，避免经一键下载电路复位单片机."""
    port = _factory()
    port.port = path
    port.baudrate = BAUDRATE
    port.timeout = timeout_s
    port.dtr = False
    port.rts = False
    port.open()
    return port


class CarLink:
    """按序号发送命令并解析回传帧；非法行只计数不抛出."""

    def __init__(self, _port: SerialPort) -> None:
        """注入已打开的串口."""
        self._port: SerialPort = _port
        self._buffer: bytearray = bytearray()
        self._seq: int = 0
        # 取消运动时停止帧可能与线程池中未写完的速度帧并发，整帧写入才不会交错。
        self._write_lock: threading.Lock = threading.Lock()
        self.invalid_lines: int = 0

    def next_seq(self) -> int:
        """返回下一个循环序号."""
        with self._write_lock:
            self._seq = (self._seq + 1) & 0xFFFF
            return self._seq

    def send(self, frame: bytes) -> None:
        """发送一条已编码的命令帧；多线程调用时整帧串行写入."""
        with self._write_lock:
            _ = self._port.write(frame)

    def poll(self, max_bytes: int = 4096) -> list[CarFrame]:
        """读取当前可得字节，返回其中完整且合法的帧."""
        self._buffer.extend(self._port.read(max_bytes))
        frames: list[CarFrame] = []
        while (end := self._buffer.find(b"\n")) >= 0:
            raw = bytes(self._buffer[:end]).rstrip(b"\r")
            del self._buffer[: end + 1]
            start = raw.rfind(b"$")
            if start < 0:
                continue
            try:
                frames.append(parse_frame(raw[start:]))
            except ValueError:
                self.invalid_lines += 1
                logger.debug("[CarLink.poll] 丢弃非法行 %r", raw[:40])
        # 没有换行的超长残留属于噪声，只保留最后一个起始符之后的内容
        if len(self._buffer) > MAX_INPUT_FRAME:
            start = self._buffer.rfind(b"$")
            del self._buffer[: start if start >= 0 else len(self._buffer)]
        return frames

    def close(self) -> None:
        """发送停车命令后关闭串口；停车失败也会关闭."""
        try:
            self.send(stop(self.next_seq()))
        finally:
            self._port.close()
