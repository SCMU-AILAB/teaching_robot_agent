"""异步模拟设备，支持正常、失败和挂起三种执行模式。"""

import asyncio
import math
import time
from enum import StrEnum
from typing import override

from domain.models import Pose2D, RobotState
from domain.validation import finite_float
from robot.base import RobotAdapter


class SimulationMode(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    HANG = "hang"


class SimulatedAdapter(RobotAdapter):
    is_simulated: bool = True

    def __init__(
        self,
        mode: SimulationMode = SimulationMode.SUCCESS,
        time_scale: float = 0.01,
    ) -> None:
        scale = finite_float(time_scale, "time_scale")
        if scale <= 0:
            raise ValueError("time_scale must be a finite positive number")
        self.mode: SimulationMode = SimulationMode(mode)
        self.time_scale: float = scale
        self._connected: bool = False
        self._moving: bool = False
        self._position: Pose2D = Pose2D()
        self._updated_at: float = time.time()
        self._stop_event: asyncio.Event | None = None

    @override
    async def connect(self) -> None:
        self._connected = True
        self._updated_at = time.time()

    @override
    async def disconnect(self) -> None:
        await self.stop()
        self._connected = False
        self._updated_at = time.time()

    @override
    async def get_state(self) -> RobotState:
        return RobotState(
            is_connected=self._connected,
            is_moving=self._moving,
            position=self._position,
            updated_at=self._updated_at,
        )

    @override
    async def move_relative(self, distance_m: float, speed_m_s: float) -> None:
        distance_m = finite_float(distance_m, "distance_m")
        speed_m_s = finite_float(speed_m_s, "speed_m_s")
        if speed_m_s <= 0:
            raise ValueError("speed_m_s must be positive")
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

        duration = abs(distance_m) / speed_m_s * self.time_scale
        started_at = time.monotonic()
        while not stop_event.is_set():
            elapsed = time.monotonic() - started_at
            fraction = min(elapsed / duration, 1.0) if duration else 1.0
            if self.mode == SimulationMode.FAILURE:
                fraction = min(fraction, 0.5)
            self._position = Pose2D(
                x=origin.x + distance_m * fraction * math.cos(origin.yaw),
                y=origin.y + distance_m * fraction * math.sin(origin.yaw),
                yaw=origin.yaw,
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
        if self._stop_event is not None:
            self._stop_event.set()
        self._moving = False
        self._updated_at = time.time()
