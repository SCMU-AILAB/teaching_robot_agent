# robot/car_adapter.py
"""麦克纳姆底盘适配器：经串口协议驱动 STM32 固件，主机端由编码器遥测推算里程计.

固件阶段 1 只接受归一化速度，位姿由本适配器按标定常数积分；里程计看不到打滑，
声明 LOCALIZATION 只表示“编码器里程计”，不是外部定位。未标定时不声明任何运动能力。
"""

import asyncio
import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from domain.models import Pose2D, RobotState
from domain.robot import RobotCapability
from domain.validation import finite_float, nonempty_string
from robot.base import RobotAdapter
from robot.car_link import (
    Ack,
    CarLink,
    SerialPort,
    Telemetry,
    open_serial,
    stop,
    velocity,
)

logger = logging.getLogger(__name__)

# 固件满量程 1000 千分比 = 每轮 4000 编码器计数/秒。
_COUNTS_PER_PERMILLE = 4.0
_STATE_MOVING = 1
_STATE_FAULT = 3
_I32 = 1 << 32
# 停车确认期限，秒：停车后电机滑行，最高车速（每轮 2400 计数/秒）滑行到静止约 0.9 秒。
_HALT_TIMEOUT_S = 2.0
# 单帧（50 ms）编码器增量不超过该值视为静止：正交编码器停在边沿时可能来回跳 1 个计数。
_STILL_COUNTS = 1


@dataclass(frozen=True)
class CarConfig:
    """串口与里程计标定；counts_per_* 为 None 表示未标定.

    Attributes:
        port: 串口设备路径.
        counts_per_meter: 前进 1 米时四轮平均编码器计数.
        counts_per_radian: 逆时针旋转 1 弧度时 (-左前+右前-左后+右后)/4 的计数.
        counts_per_meter_lateral: 向左平移 1 米时 (-左前+右前+左后-右后)/4 的计数；
            None 时取 counts_per_meter.
        max_permille: 单轴速度千分比上限，限制最高车速.
        position_tolerance_m: 相对移动验收容差，米.
        yaw_tolerance_rad: 转向验收容差，弧度.
        coast_counts_per_s2: 停车后每轮转速的滑行减速度，计数/秒²；固件占空为 0 时
            电机两端均为低电平，不短接制动。实测落地 10 cm/s 停车后滑行约 3.3 cm.
    """

    port: str
    counts_per_meter: float | None = None
    counts_per_radian: float | None = None
    counts_per_meter_lateral: float | None = None
    max_permille: int = 600
    position_tolerance_m: float = 0.02
    yaw_tolerance_rad: float = 0.035
    coast_counts_per_s2: float = 2700.0

    def __post_init__(self) -> None:
        """拒绝非正标定值与越界速度上限."""
        _ = nonempty_string(self.port, "port")
        for name, value in (
            ("counts_per_meter", self.counts_per_meter),
            ("counts_per_radian", self.counts_per_radian),
            ("counts_per_meter_lateral", self.counts_per_meter_lateral),
        ):
            if value is not None and finite_float(value, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if isinstance(self.max_permille, bool) or not 0 < self.max_permille <= 1000:
            raise ValueError("max_permille must be in (0, 1000]")
        for name, value in (
            ("position_tolerance_m", self.position_tolerance_m),
            ("yaw_tolerance_rad", self.yaw_tolerance_rad),
            ("coast_counts_per_s2", self.coast_counts_per_s2),
        ):
            if finite_float(value, name) <= 0:
                raise ValueError(f"{name} must be positive")

    @property
    def calibrated(self) -> bool:
        """前进与旋转标定齐全."""
        return self.counts_per_meter is not None and self.counts_per_radian is not None


def _optional_float(name: str) -> float | None:
    """读取可选浮点环境变量."""
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else None


def car_config_from_env() -> CarConfig:
    """按 CAR_* 环境变量构造配置，缺少串口路径直接报错.

    Raises:
        ValueError: CAR_SERIAL_PORT 缺失或数值配置非法.
    """
    return CarConfig(
        port=nonempty_string(os.environ.get("CAR_SERIAL_PORT"), "CAR_SERIAL_PORT"),
        counts_per_meter=_optional_float("CAR_COUNTS_PER_METER"),
        counts_per_radian=_optional_float("CAR_COUNTS_PER_RADIAN"),
        counts_per_meter_lateral=_optional_float("CAR_COUNTS_PER_METER_LATERAL"),
        max_permille=int(os.environ.get("CAR_MAX_PERMILLE", "600")),
        position_tolerance_m=float(os.environ.get("CAR_POSITION_TOLERANCE_M", "0.02")),
        yaw_tolerance_rad=float(os.environ.get("CAR_YAW_TOLERANCE_RAD", "0.035")),
        coast_counts_per_s2=float(os.environ.get("CAR_COAST_COUNTS_PER_S2", "2700")),
    )


def _wrap_delta(new: int, old: int) -> int:
    """i32 累计计数的差值，处理回绕."""
    return (new - old + (1 << 31)) % _I32 - (1 << 31)


def _clamp(value: float, limit: float) -> float:
    """限制到 ±limit."""
    return max(-limit, min(limit, value))


class CarAdapter(RobotAdapter):
    """独占一个串口；后台读取遥测，运动方法以 20 Hz 发送速度直到里程计到位."""

    is_simulated: bool = False
    # 固件每 50 ms 上报一次，超过 0.5 秒没有遥测即视为断开。
    telemetry_max_age_s: float = 0.5

    def __init__(
        self,
        _config: CarConfig,
        *,
        _open_port: Callable[[str], SerialPort] = open_serial,
        _telemetry_timeout_s: float = 0.3,
    ) -> None:
        """保存配置，构造期间不打开串口.

        Args:
            _config: 串口与标定配置.
            _open_port: 打开串口的函数，测试时替换为固件替身.
            _telemetry_timeout_s: 运动中等待下一帧遥测的上限，秒.
        """
        self._config: CarConfig = _config
        self._open_port: Callable[[str], SerialPort] = _open_port
        self._telemetry_timeout_s: float = finite_float(
            _telemetry_timeout_s, "telemetry_timeout_s"
        )
        self.position_tolerance_m: float = _config.position_tolerance_m
        self.yaw_tolerance_rad: float = _config.yaw_tolerance_rad
        self._link: CarLink | None = None
        self._reader: asyncio.Task[None] | None = None
        self._reading: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()
        self._tick: asyncio.Event = asyncio.Event()
        self._encoders: tuple[int, int, int, int] | None = None
        self._pose: Pose2D = Pose2D()
        # 车体坐标系速度（前进 m/s、左移 m/s、逆时针 rad/s），按固件时间戳由相邻两帧求得。
        self._velocity: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._telemetry_ms: int | None = None
        self._fw_state: int = 0
        self._wheels_turning: bool = False
        self._telemetry_at: float = 0.0
        self._telemetry_count: int = 0
        self._rejected: int | None = None
        self._read_error: str | None = None

    @property
    @override
    def capabilities(self) -> frozenset[RobotCapability]:
        """标定齐全才开放编码器里程计与相对运动."""
        if not self._config.calibrated:
            return frozenset()
        return frozenset(
            {
                RobotCapability.LOCALIZATION,
                RobotCapability.MOVE_RELATIVE,
                RobotCapability.TURN_RELATIVE,
            }
        )

    @override
    def motion_timeout_s(self, nominal_s: float) -> float:
        """覆盖 _drive 的期限（2 倍估算加 3 秒）及到位后的停车确认."""
        return nominal_s * 2 + 3.0 + _HALT_TIMEOUT_S + 1.0

    # ========== 连接与遥测 ==========

    @override
    async def connect(self) -> None:
        """打开串口、启动读取并发送停止帧锁定通信口，等待首帧遥测.

        Raises:
            RuntimeError: 2 秒内没有收到遥测.
        """
        async with self._lock:
            if self._link is not None:
                return
            port = await asyncio.to_thread(self._open_port, self._config.port)
            self._link = CarLink(port)
            self._read_error = None
            self._reading = True
            self._reader = asyncio.create_task(self._read_loop(), name="car-telemetry")
            try:
                # 固件收到第一条合法命令才锁定端口并开始上报；停止同时解除故障锁定。
                await self._send(stop(self._link.next_seq()))
                if not await self._wait_ticks(1, 2.0):
                    raise RuntimeError("Car sent no telemetry after connect")
            except BaseException:
                await self._release()
                raise
            logger.info(
                "[CarAdapter.connect] 底盘已连接 port=%s calibrated=%s",
                self._config.port,
                self._config.calibrated,
            )

    @override
    async def disconnect(self) -> None:
        """停车并释放串口；停车确认失败也释放，重复调用安全."""
        async with self._lock:
            if self._link is None:
                return
            try:
                await self._halt()
            except RuntimeError as error:
                logger.warning("[CarAdapter.disconnect] 停车未确认 error=%s", error)
            finally:
                await self._release()

    async def _release(self) -> None:
        """结束读取后关闭串口，避免读线程与关闭并发."""
        self._reading = False
        if self._reader is not None:
            _ = await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        link, self._link = self._link, None
        if link is not None:
            await asyncio.to_thread(link.close)

    async def _read_loop(self) -> None:
        """在线程中读取串口，帧在事件循环中处理；串口异常记录后结束."""
        link = self._link
        if link is None:
            return
        while self._reading:
            try:
                frames = await asyncio.to_thread(link.poll)
            except Exception as error:
                self._read_error = f"{type(error).__name__}: {error}"
                logger.error("[CarAdapter._read_loop] 串口读取失败 error=%r", error)
                self._signal()
                return
            for frame in frames:
                if isinstance(frame, Telemetry):
                    self._on_telemetry(frame)
                elif isinstance(frame, Ack) and frame.code != 0:
                    self._rejected = frame.code
                    logger.warning(
                        "[CarAdapter._read_loop] 命令被拒绝 seq=%s code=%s",
                        frame.seq,
                        frame.code,
                    )

    def _on_telemetry(self, frame: Telemetry) -> None:
        """按轮位换算编码器增量并积分里程计；轮位：左前 M4、右前 M1、左后 M3、右后 M2."""
        previous, self._encoders = self._encoders, frame.encoders
        previous_ms, self._telemetry_ms = self._telemetry_ms, frame.ms
        self._fw_state = frame.state
        self._telemetry_at = time.time()
        self._telemetry_count += 1
        if previous is not None:
            m1, m2, m3, m4 = (
                _wrap_delta(new, old)
                for new, old in zip(frame.encoders, previous, strict=True)
            )
            front_left, front_right, rear_left, rear_right = m4, m1, m3, m2
            self._wheels_turning = any(
                abs(item) > _STILL_COUNTS for item in (m1, m2, m3, m4)
            )
            body = self._integrate(front_left, front_right, rear_left, rear_right)
            elapsed_ms = (
                0 if previous_ms is None else _wrap_delta(frame.ms, previous_ms)
            )
            if body is not None and elapsed_ms > 0:
                forward, left, turn = body
                seconds = elapsed_ms / 1000
                self._velocity = (forward / seconds, left / seconds, turn / seconds)
        self._signal()

    def _integrate(
        self, fl: int, fr: int, rl: int, rr: int
    ) -> tuple[float, float, float] | None:
        """麦克纳姆正运动学，取半步航向把车体位移转到里程计坐标系.

        Returns:
            本帧车体位移（前进米、左移米、逆时针弧度）；未标定返回 None.
        """
        config = self._config
        if config.counts_per_meter is None or config.counts_per_radian is None:
            return None
        lateral = config.counts_per_meter_lateral or config.counts_per_meter
        forward = (fl + fr + rl + rr) / 4 / config.counts_per_meter
        left = (-fl + fr + rl - rr) / 4 / lateral
        turn = (-fl + fr - rl + rr) / 4 / config.counts_per_radian
        heading = self._pose.yaw + turn / 2
        self._pose = Pose2D(
            self._pose.x + forward * math.cos(heading) - left * math.sin(heading),
            self._pose.y + forward * math.sin(heading) + left * math.cos(heading),
            self._pose.yaw + turn,
        )
        return forward, left, turn

    def _calibration(self) -> tuple[float, float]:
        """返回前进与旋转标定.

        Raises:
            RuntimeError: 未标定.
        """
        config = self._config
        if config.counts_per_meter is None or config.counts_per_radian is None:
            raise RuntimeError("Car odometry is not calibrated")
        return config.counts_per_meter, config.counts_per_radian

    def _coast(self, speed: float, counts_per_unit: float) -> float:
        """当前速度下停车后的滑行量（与速度同号），单位与 speed 的长度或角度单位一致."""
        return (
            speed
            * abs(speed)
            * counts_per_unit
            / (2 * self._config.coast_counts_per_s2)
        )

    @staticmethod
    def _arrived(remaining: float, coast: float, tolerance: float) -> bool:
        """计入停车滑行后到位：预计停点落在容差内，或继续行驶只会冲过目标."""
        projected = remaining - coast
        return abs(projected) <= tolerance or projected * remaining < 0

    def _signal(self) -> None:
        """唤醒等待下一帧遥测的协程."""
        self._tick.set()
        self._tick = asyncio.Event()

    async def _wait_ticks(self, count: int, timeout_s: float) -> bool:
        """等待新收到 count 帧遥测；超时或读取中断返回 False."""
        target = self._telemetry_count + count
        try:
            async with asyncio.timeout(timeout_s):
                while self._telemetry_count < target:
                    if self._read_error is not None:
                        return False
                    _ = await self._tick.wait()
        except TimeoutError:
            return False
        return True

    async def _send(self, frame: bytes) -> None:
        """发送一帧；未连接时报错."""
        link = self._link
        if link is None:
            raise RuntimeError("Car is not connected")
        await asyncio.to_thread(link.send, frame)

    def _connected(self) -> bool:
        """串口打开、读取正常且遥测新鲜."""
        return (
            self._link is not None
            and self._read_error is None
            and time.time() - self._telemetry_at <= self.telemetry_max_age_s
        )

    @override
    async def get_state(self) -> RobotState:
        """返回最近一帧遥测对应的状态；updated_at 为该帧的接收时间."""
        return RobotState(
            is_connected=self._connected(),
            is_moving=self._fw_state == _STATE_MOVING or self._wheels_turning,
            position=self._pose,
            updated_at=self._telemetry_at or time.time() - 3600,
        )

    # ========== 停止 ==========

    @override
    async def stop(self) -> None:
        """发送停止并等待遥测确认四轮静止.

        Raises:
            RuntimeError: 1 秒内未确认静止或串口不可用.
        """
        await self._halt()

    async def _halt(self) -> None:
        """立即停车（不受斜率限制），连续两帧四轮无增量才算确认."""
        link = self._link
        if link is None:
            raise RuntimeError("Car is not connected")
        await self._send(stop(link.next_seq()))
        deadline = time.monotonic() + _HALT_TIMEOUT_S
        still = 0
        while still < 2:
            if not await self._wait_ticks(1, max(0.0, deadline - time.monotonic())):
                raise RuntimeError("Could not confirm that the car stopped")
            still = still + 1 if not self._wheels_turning else 0
        self._rejected = None

    # ========== 运动 ==========

    @override
    async def move_relative(self, distance_m: float, speed_m_s: float) -> None:
        """沿起始朝向移动，按里程计减速停在目标点，同时修正侧向与航向偏差."""
        distance = finite_float(distance_m, "distance_m")
        speed = finite_float(speed_m_s, "speed_m_s")
        if speed <= 0:
            raise ValueError("speed_m_s must be positive")
        counts_per_meter, _ = self._calibration()
        start = self._pose
        cos_h, sin_h = math.cos(start.yaw), math.sin(start.yaw)

        def command(pose: Pose2D) -> tuple[float, float, float] | None:
            dx, dy = pose.x - start.x, pose.y - start.y
            remaining = distance - (dx * cos_h + dy * sin_h)
            # 停车后还会滑行，按实测速度提前停车。
            coast = self._coast(self._velocity[0], counts_per_meter)
            if self._arrived(remaining, coast, self.position_tolerance_m / 4):
                return None
            lateral = -dx * sin_h + dy * cos_h
            forward = math.copysign(
                min(speed, max(0.06, math.sqrt(2 * 0.3 * abs(remaining)))), remaining
            )
            return (
                forward,
                _clamp(-2.0 * lateral, 0.05),
                _clamp(2.0 * math.remainder(start.yaw - pose.yaw, math.tau), 0.3),
            )

        await self._drive(command, abs(distance) / speed)

    @override
    async def turn_relative(self, angle_rad: float, speed_rad_s: float) -> None:
        """原地转到起始航向 + angle，同时把位置保持在起点."""
        angle = finite_float(angle_rad, "angle_rad")
        speed = finite_float(speed_rad_s, "speed_rad_s")
        if speed <= 0:
            raise ValueError("speed_rad_s must be positive")
        _, counts_per_radian = self._calibration()
        start = self._pose
        target = start.yaw + angle

        def command(pose: Pose2D) -> tuple[float, float, float] | None:
            remaining = target - pose.yaw
            coast = self._coast(self._velocity[2], counts_per_radian)
            if self._arrived(remaining, coast, self.yaw_tolerance_rad / 4):
                return None
            dx, dy = start.x - pose.x, start.y - pose.y
            cos_h, sin_h = math.cos(pose.yaw), math.sin(pose.yaw)
            spin = math.copysign(
                min(speed, max(0.15, math.sqrt(2 * 1.0 * abs(remaining)))), remaining
            )
            return (
                _clamp(2.0 * (dx * cos_h + dy * sin_h), 0.05),
                _clamp(2.0 * (-dx * sin_h + dy * cos_h), 0.05),
                spin,
            )

        await self._drive(command, abs(angle) / speed)

    async def _drive(
        self,
        command: Callable[[Pose2D], tuple[float, float, float] | None],
        nominal_s: float,
    ) -> None:
        """每帧遥测发送一次速度，直到 command 返回 None；返回前确认静止.

        Args:
            command: 由当前里程计位姿给出 (前进 m/s, 左移 m/s, 逆时针 rad/s)，到位返回 None.
            nominal_s: 按请求速度估算的耗时，期限为其 2 倍加 3 秒.

        Raises:
            RuntimeError: 未连接、未标定、底盘故障、命令被拒、遥测中断或超出期限.
        """
        config = self._config
        counts_per_meter, counts_per_radian = self._calibration()
        link = self._link
        if link is None or not self._connected():
            raise RuntimeError("Car is not connected")
        lateral = config.counts_per_meter_lateral or counts_per_meter
        deadline = time.monotonic() + nominal_s * 2 + 3.0
        self._rejected = None
        try:
            while True:
                if self._fw_state == _STATE_FAULT:
                    raise RuntimeError("Car reported stall or encoder fault")
                if self._rejected is not None:
                    raise RuntimeError(f"Car rejected command code={self._rejected}")
                if time.monotonic() > deadline:
                    raise RuntimeError("Car did not reach the target in time")
                step = command(self._pose)
                if step is None:
                    break
                forward, left, spin = step
                limit = config.max_permille
                # 千分比 = 每秒计数 / 4；看门狗 300 ms，每帧遥测（50 ms）刷新一次。
                frame = velocity(
                    link.next_seq(),
                    round(
                        _clamp(
                            forward * counts_per_meter / _COUNTS_PER_PERMILLE,
                            limit,
                        )
                    ),
                    round(_clamp(left * lateral / _COUNTS_PER_PERMILLE, limit)),
                    round(
                        _clamp(
                            spin * counts_per_radian / _COUNTS_PER_PERMILLE,
                            limit,
                        )
                    ),
                )
                await self._send(frame)
                if not await self._wait_ticks(1, self._telemetry_timeout_s):
                    raise RuntimeError("Car telemetry interrupted during motion")
        except BaseException:
            # 失败或取消先立即发停止；静止确认由 Runtime 清理阶段的 stop() 完成。
            link = self._link
            if link is not None:
                try:
                    link.send(stop(link.next_seq()))
                except Exception as error:
                    logger.error("[CarAdapter._drive] 停止帧发送失败 error=%r", error)
            raise
        await self._halt()
