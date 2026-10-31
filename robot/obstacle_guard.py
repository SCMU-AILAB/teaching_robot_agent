# robot/obstacle_guard.py
"""深度避障守护：包在底盘适配器外层，前进途中前方空隙不足或看不清前方时停车并报错."""

import asyncio
import logging
import time
from typing import Protocol, override

from domain.models import RobotState
from domain.robot import NavigationGoal, RobotCapability
from domain.validation import finite_float
from perception.depth.geometry import Clearance
from robot.base import RobotAdapter

logger = logging.getLogger(__name__)

_POLL_S = 0.1
# 贴近障碍判断：上一读数已近于该距离、本次画面中部有效读数降到一半以下
_NEAR_M = 0.6
_VALID_DROP = 0.5


class ObstacleError(RuntimeError):
    """前方有障碍或看不清前方，已停车."""


class DepthSource(Protocol):
    """带生命周期的空隙来源."""

    def start(self) -> None:
        """打开设备并开始更新."""
        ...

    def close(self) -> None:
        """停止更新并释放设备."""
        ...

    def latest(self) -> Clearance | None:
        """返回最近一次计算的空隙."""
        ...


class GuardedRobotAdapter(RobotAdapter):
    """前进时每 0.1 秒检查正前方空隙；后退限距，转向原样转发."""

    def __init__(
        self,
        _inner: RobotAdapter,
        _depth: DepthSource,
        *,
        _stop_distance_m: float = 0.4,
        _max_reverse_m: float = 0.3,
        _stale_s: float = 0.5,
        _decel_m_s2: float = 0.15,
        _latency_s: float = 0.2,
    ) -> None:
        """注入底盘与深度来源；构造时不打开设备.

        Args:
            _inner: 实际底盘适配器.
            _depth: 深度空隙来源.
            _stop_distance_m: 停车距离（不含滑行量），从摄像头算起.
            _max_reverse_m: 单次后退上限；摄像头看不到后方.
            _stale_s: 空隙超过该时长未更新即视为看不清前方.
            _decel_m_s2: 停车后滑行减速度，用于估算滑行量.
            _latency_s: 检测到停车生效的延迟，按当前速度折算距离.
        """
        self._inner: RobotAdapter = _inner
        self._depth: DepthSource = _depth
        self._stop_m: float = _positive(_stop_distance_m, "stop_distance_m")
        self._max_reverse_m: float = _positive(_max_reverse_m, "max_reverse_m")
        self._stale_s: float = _positive(_stale_s, "stale_s")
        self._decel: float = _positive(_decel_m_s2, "decel_m_s2")
        self._latency_s: float = _positive(_latency_s, "latency_s")
        self.is_simulated: bool = _inner.is_simulated
        self.telemetry_max_age_s: float = _inner.telemetry_max_age_s
        self.position_tolerance_m: float = _inner.position_tolerance_m
        self.yaw_tolerance_rad: float = _inner.yaw_tolerance_rad
        self.max_reverse_m: float | None = self._max_reverse_m

    @property
    def depth(self) -> DepthSource:
        """公开深度来源，供探索选择方向."""
        return self._depth

    @property
    @override
    def capabilities(self) -> frozenset[RobotCapability]:
        """能力与底盘一致."""
        return self._inner.capabilities

    @override
    def validate_state(self, state: RobotState, *, after: float | None = None) -> None:
        """按底盘规则校验遥测."""
        self._inner.validate_state(state, after=after)

    @override
    def motion_timeout_s(self, nominal_s: float) -> float:
        """按底盘规则估算运动期限."""
        return self._inner.motion_timeout_s(nominal_s)

    @override
    async def connect(self) -> None:
        """先启动深度相机再连接底盘；深度相机失败只记日志，前进会因看不清而拒绝."""
        try:
            await asyncio.to_thread(self._depth.start)
        except Exception:
            logger.exception("[GuardedRobotAdapter.connect] 深度相机启动失败")
        await self._inner.connect()

    @override
    async def disconnect(self) -> None:
        """断开底盘并关闭深度相机."""
        try:
            await self._inner.disconnect()
        finally:
            await asyncio.to_thread(self._depth.close)

    @override
    async def get_state(self) -> RobotState:
        """返回底盘状态."""
        return await self._inner.get_state()

    @override
    async def turn_relative(self, angle_rad: float, speed_rad_s: float) -> None:
        """原地转向不检查前方空隙，由底盘自身的堵转与超时保护兜底."""
        await self._inner.turn_relative(angle_rad, speed_rad_s)

    @override
    async def navigate_to(self, goal: NavigationGoal) -> None:
        """导航由设备自带的避障负责."""
        await self._inner.navigate_to(goal)

    @override
    async def stop(self) -> None:
        """停止底盘."""
        await self._inner.stop()

    @override
    async def move_relative(self, distance_m: float, speed_m_s: float) -> None:
        """前进途中前方空隙不足、贴近障碍或深度过期时停车并抛出 ObstacleError.

        Raises:
            ValueError: 后退超过上限。
            ObstacleError: 前方有障碍或看不清前方，已停车。
        """
        distance = finite_float(distance_m, "distance_m")
        speed = finite_float(speed_m_s, "speed_m_s")
        if distance < 0:
            if -distance > self._max_reverse_m:
                raise ValueError(
                    f"Reverse is limited to {self._max_reverse_m:g} m without rear sensing"
                )
            await self._inner.move_relative(distance, speed)
            return
        reading = self._depth.latest()
        reason = self._blocked(speed, None, reading)
        if reason is not None:
            raise ObstacleError(reason)
        move = asyncio.create_task(self._inner.move_relative(distance, speed))
        try:
            while True:
                done, _ = await asyncio.wait({move}, timeout=_POLL_S)
                if done:
                    break
                current = self._depth.latest()
                reason = self._blocked(speed, reading, current)
                reading = current
                if reason is not None:
                    await _cancel(move)
                    await self._inner.stop()
                    logger.info(
                        "[GuardedRobotAdapter.move_relative] 前进途中停车 reason=%s",
                        reason,
                    )
                    raise ObstacleError(reason)
        except asyncio.CancelledError:
            await _cancel(move)
            raise
        await move

    def stop_margin_m(self, speed_m_s: float) -> float:
        """按该速度前进时需要的前方空隙：停车距离加滑行量与反应距离."""
        speed = abs(speed_m_s)
        return (
            self._stop_m + speed * speed / (2 * self._decel) + speed * self._latency_s
        )

    def _blocked(
        self, speed: float, previous: Clearance | None, current: Clearance | None
    ) -> str | None:
        """返回停车原因；可以继续前进时为 None."""
        if current is None or time.time() - current.captured_at > self._stale_s:
            return "看不清前方，已停下"
        if current.ahead_m < self.stop_margin_m(speed):
            return "前方有障碍物，已停下"
        if (
            previous is not None
            and previous.ahead_m < _NEAR_M
            and current.center_valid_ratio < previous.center_valid_ratio * _VALID_DROP
        ):
            return "前方有障碍物，已停下"
        return None


async def _cancel(task: asyncio.Task[None]) -> None:
    """取消运动协程并等待其结束；底盘取消时会立即发停车帧."""
    _ = task.cancel()
    _ = await asyncio.gather(task, return_exceptions=True)


def _positive(value: float, name: str) -> float:
    """校验正数."""
    number = finite_float(value, name)
    if number <= 0:
        raise ValueError(f"{name} must be positive")
    return number
