# tests/car_firmware_fixture.py
"""按阶段 1 固件协议行为编写的串口替身：校验命令帧、应答、20 Hz 遥测、看门狗与故障锁定."""

import threading
import time

from robot.car_link import crc8

_FULL_COUNTS_PER_S = 4000.0
# 固件斜率：每 10 ms 每轮 20 千分比，即每秒 2000 千分比。
_RAMP_PERMILLE_PER_S = 2000.0
_WATCHDOG_S = 0.3
_TELEMETRY_S = 0.05


def _frame(payload: str) -> bytes:
    """编码一条小车发出的帧."""
    return f"${payload}*{crc8(payload.encode()):02X}\n".encode()


def _mix(vx: int, vy: int, wz: int) -> list[float]:
    """与 car-core mix_mecanum 一致：左前、右前、左后、右后，超限等比缩放."""
    wheels = (vx - vy - wz, vx + vy + wz, vx + vy - wz, vx - vy + wz)
    scale = max(1000, *(abs(item) for item in wheels))
    return [item * 1000 / scale for item in wheels]


class FirmwareSerial:
    """线程安全；读取在工作线程、写入在事件循环线程."""

    def __init__(
        self,
        _encoder_offset: int = 0,
        _coast_counts_per_s2: float | None = None,
        _response_s: float | None = None,
    ) -> None:
        """encoder_offset 用于测试 i32 回绕.

        coast_counts_per_s2 为停车后每轮滑行减速度（计数/秒²），与真车一致：固件占空为 0
        时电机不制动；None 时停止命令立即使轮子静止。response_s 为落地轮速跟随目标的
        一阶时间常数（真车 PI 响应约 0.4 秒）；None 时只受固件斜率限制.
        """
        self._lock: threading.Lock = threading.Lock()
        self._out: bytearray = bytearray(_frame("B,1,1"))
        self._in: bytearray = bytearray()
        self._locked: bool = False
        self._state: int = 0
        self._fault: bool = False
        self._target: list[float] = [0.0] * 4  # 电机 M1..M4，千分比
        self._speed: list[float] = [0.0] * 4
        self._encoders: list[float] = [float(_encoder_offset)] * 4
        self._last_motion: float = 0.0
        self._last_tick: float = time.monotonic()
        self._next_telemetry: float = 0.0
        self.silent: bool = False
        # 静止时编码器停在边沿来回跳 1 个计数，与真实正交编码器一致。
        self.jitter: bool = False
        self._coast: float | None = _coast_counts_per_s2
        self._coasting: bool = False
        self._response: float | None = _response_s
        self._jitter_phase: int = 0
        self.closed: bool = False
        self.commands: list[str] = []

    # ========== SerialPort ==========

    def read(self, size: int = 1) -> bytes:
        """推进模拟时间并返回待发送字节；没有数据时阻塞至多 20 ms."""
        with self._lock:
            self._advance()
            empty = not self._out
        if empty:
            time.sleep(0.02)
        with self._lock:
            self._advance()
            data = bytes(self._out[:size])
            del self._out[:size]
            return data

    def write(self, data: bytes, /) -> int:
        """接收命令字节，按行处理."""
        with self._lock:
            if self.closed:
                raise OSError("port closed")
            self._advance()
            self._in.extend(data)
            while (end := self._in.find(b"\n")) >= 0:
                line = bytes(self._in[:end]).strip()
                del self._in[: end + 1]
                self._handle(line)
            return len(data)

    def close(self) -> None:
        """关闭端口."""
        with self._lock:
            self.closed = True

    # ========== 测试控制 ==========

    def inject_fault(self) -> None:
        """模拟堵转：输出归零并锁定，直到收到停止."""
        with self._lock:
            self._fault = True
            self._state = 3
            self._target = [0.0] * 4
            self._speed = [0.0] * 4

    @property
    def speeds(self) -> tuple[float, ...]:
        """当前四个电机的实际转速千分比."""
        with self._lock:
            return tuple(self._speed)

    # ========== 固件行为 ==========

    def _handle(self, line: bytes) -> None:
        """校验并执行一条命令."""
        if not line.startswith(b"$") or b"*" not in line:
            self._out.extend(_frame("E,3"))
            return
        payload, _, checksum = line[1:].rpartition(b"*")
        if f"{crc8(payload):02X}".encode() != checksum:
            self._out.extend(_frame("E,1"))
            return
        name, *fields = payload.decode().split(",")
        values = [int(item) for item in fields]
        seq = values[0]
        self.commands.append(name)
        self._locked = True
        now = time.monotonic()
        code = 0
        if name == "S":
            self._fault = False
            self._state = 0
            self._target = [0.0] * 4
            if self._coast is None:
                self._speed = [0.0] * 4
            else:
                self._coasting = True
        elif name == "V":
            if self._fault:
                code = 5
            else:
                fl, fr, rl, rr = _mix(values[1], values[2], values[3])
                # 轮位到电机：左前 M4、右前 M1、左后 M3、右后 M2。
                self._target = [fr, rr, rl, fl]
                self._state = 1
                self._coasting = False
                self._last_motion = now
        elif name != "P":
            code = 3
        self._out.extend(_frame(f"A,{seq},{code}"))

    def _advance(self) -> None:
        """按真实流逝时间积分转速与编码器，处理看门狗并排出遥测."""
        now = time.monotonic()
        while self._last_tick < now:
            step = min(0.01, now - self._last_tick)
            self._last_tick += step
            if self._state == 1 and self._last_tick - self._last_motion > _WATCHDOG_S:
                self._state = 2
                self._target = [0.0] * 4
            for index in range(4):
                delta = self._target[index] - self._speed[index]
                limit = (
                    self._coast / _FULL_COUNTS_PER_S * 1000 * step
                    if self._coasting and self._coast is not None
                    else _RAMP_PERMILLE_PER_S * step
                )
                if not self._coasting and self._response is not None:
                    delta *= min(1.0, step / self._response)
                self._speed[index] += max(-limit, min(limit, delta))
                self._encoders[index] += (
                    self._speed[index] / 1000 * _FULL_COUNTS_PER_S * step
                )
            if (
                self._locked
                and not self.silent
                and self._last_tick >= self._next_telemetry
            ):
                self._next_telemetry = self._last_tick + _TELEMETRY_S
                offset = 0
                if self.jitter:
                    self._jitter_phase ^= 1
                    offset = self._jitter_phase
                counts = [
                    (round(value) + offset + (1 << 31)) % (1 << 32) - (1 << 31)
                    for value in self._encoders
                ]
                duties = [round(value * 0.37) for value in self._speed]
                fields = ",".join(str(item) for item in (*counts, *duties))
                self._out.extend(
                    _frame(
                        f"T,{int(self._last_tick * 1000) % (1 << 32)},{self._state},{fields}"
                    )
                )
