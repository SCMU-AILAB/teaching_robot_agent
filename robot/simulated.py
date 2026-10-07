# robot/simulated.py
"""异步模拟设备，支持正常、失败和挂起三种执行模式."""

import asyncio
import math
import time
from enum import StrEnum
from typing import override

from domain.models import Pose2D, RobotState
from domain.robot import RobotCapability
from domain.validation import finite_float
from robot.base import RobotAdapter


class SimulationMode(StrEnum):
    """模拟设备的成功、失败和挂起模式."""

    SUCCESS = "success"
    FAILURE = "failure"
    HANG = "hang"


class SimulatedAdapter(RobotAdapter):
    """在内存中模拟相对移动及设备异常."""

    is_simulated: bool = True

    @property
    @override
    def capabilities(self) -> frozenset[RobotCapability]:
        """模拟器提供里程计、相对移动与转向，不宣称支持导航."""
        return frozenset(
            {
                RobotCapability.LOCALIZATION,
                RobotCapability.MOVE_RELATIVE,
                RobotCapability.TURN_RELATIVE,
            }
        )

    def __init__(
        self,
        _mode: SimulationMode = SimulationMode.SUCCESS,
        _time_scale: float = 0.01,
    ) -> None:
        """初始化依赖与实例状态，不启动后台任务."""
        scale = finite_float(_time_scale, "time_scale")
        if scale <= 0:
            raise ValueError("time_scale must be a finite positive number")
        self.mode: SimulationMode = SimulationMode(_mode)
        self.time_scale: float = scale
        self._connected: bool = False
        self._moving: bool = False
        self._position: Pose2D = Pose2D()
        self._updated_at: float = time.time()
        self._stop_event: asyncio.Event | None = None

    @override
    async def connect(self) -> None:
        """建立设备连接并更新状态."""
        self._connected = True
        self._updated_at = time.time()

    @override
    async def disconnect(self) -> None:
        """停止设备并释放连接."""
        await self.stop()
        self._connected = False
        self._updated_at = time.time()

    @override
    async def get_state(self) -> RobotState:
        """返回当前连接、运动和位姿快照."""
        return RobotState(
            is_connected=self._connected,
            is_moving=self._moving,
            position=self._position,
            updated_at=self._updated_at,
        )

    @override
    async def move_relative(self, distance_m: float, speed_m_s: float) -> None:
        """沿当前朝向移动指定距离，等待执行返回."""
        distance_m = finite_float(distance_m, "distance_m")
        speed_m_s = finite_float(speed_m_s, "speed_m_s")
        if speed_m_s <= 0:
            raise ValueError("speed_m_s must be positive")
        origin = self._position
        target = Pose2D(
            origin.x + distance_m * math.cos(origin.yaw),
            origin.y + distance_m * math.sin(origin.yaw),
            origin.yaw,
        )
        await self._move_to(target, abs(distance_m) / speed_m_s * self.time_scale)

    @override
    async def turn_relative(self, angle_rad: float, speed_rad_s: float) -> None:
        """模拟原地转向，统一复用失败、挂起及停止行为."""
        angle = finite_float(angle_rad, "angle_rad")
        speed = finite_float(speed_rad_s, "speed_rad_s")
        if speed <= 0:
            raise ValueError("speed_rad_s must be positive")
        origin = self._position
        await self._move_to(
            Pose2D(origin.x, origin.y, origin.yaw + angle),
            abs(angle) / speed * self.time_scale,
        )

    async def _move_to(self, target: Pose2D, duration: float) -> None:
        """在模拟坐标系内插值，不提供目标点导航或避障."""
        if not self._connected:
            raise RuntimeError("robot is not connected")
        if self._moving:
            raise RuntimeError("robot is already moving")

        origin = self._position
        stop_event = asyncio.Event()
        self._stop_event = stop_event
        self._moving = True
        self._updated_at = time.time()

        # 异常或协程取消不会冒充设备已经停止；Runtime 必须调用清理。
        if self.mode == SimulationMode.HANG:
            _ = await stop_event.wait()
            return

        started_at = time.monotonic()
        while not stop_event.is_set():
            elapsed = time.monotonic() - started_at
            fraction = min(elapsed / duration, 1.0) if duration else 1.0
            if self.mode == SimulationMode.FAILURE:
                fraction = min(fraction, 0.5)
            self._position = Pose2D(
                x=origin.x + (target.x - origin.x) * fraction,
                y=origin.y + (target.y - origin.y) * fraction,
                yaw=origin.yaw + (target.yaw - origin.yaw) * fraction,
            )
            self._updated_at = time.time()
            if self.mode == SimulationMode.FAILURE and fraction >= 0.5:
                raise RuntimeError("simulated movement failure")
            if fraction >= 1.0:
                self._moving = False
                return
            try:
                _ = await asyncio.wait_for(
                    stop_event.wait(), timeout=min(0.01, duration - elapsed)
                )
            except TimeoutError:
                pass

    @override
    async def stop(self) -> None:
        """请求设备停止并更新运动状态."""
        if self._stop_event is not None:
            self._stop_event.set()
        self._moving = False
        self._updated_at = time.time()
