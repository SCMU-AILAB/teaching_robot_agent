# robot/base.py
"""设备边界：只描述设备能力，不管理任务和动作队列."""

import time
from abc import ABC, abstractmethod

from domain.models import RobotState
from domain.robot import NavigationGoal, RobotCapability, UnsupportedCapabilityError
from domain.validation import finite_float


class RobotAdapter(ABC):
    """设备能力契约，具体适配器负责连接与停止确认."""

    is_simulated: bool = False
    telemetry_max_age_s: float = 2.0

    def validate_state(self, state: RobotState, *, after: float | None = None) -> None:
        """验证遥测新鲜度及命令后反馈，不把旧静止快照视为停止确认."""
        timestamp = finite_float(state.updated_at, "updated_at")
        age = time.time() - timestamp
        limit = finite_float(self.telemetry_max_age_s, "telemetry_max_age_s")
        if (
            limit <= 0
            or age < -0.1
            or age > limit
            or (after is not None and timestamp < after)
        ):
            raise RuntimeError("Robot telemetry is stale or predates the command")
        for value in (state.position.x, state.position.y, state.position.yaw):
            _ = finite_float(value, "pose")

    @property
    def capabilities(self) -> frozenset[RobotCapability]:
        """默认不声明可选能力；厂商适配器只公开实际验证的能力."""
        return frozenset()

    def require_capability(self, capability: RobotCapability) -> None:
        """在发出设备命令前拒绝不支持的操作."""
        if capability not in self.capabilities:
            raise UnsupportedCapabilityError(f"Unsupported capability: {capability}")

    @abstractmethod
    async def connect(self) -> None:
        """建立设备连接并更新状态."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """停止设备并释放连接."""
        pass

    @abstractmethod
    async def get_state(self) -> RobotState:
        """返回当前连接、运动和位姿快照."""
        pass

    async def move_relative(self, distance_m: float, speed_m_s: float) -> None:
        """沿当前朝向相对移动；返回后仍由 Skill 验证实际位姿."""
        _ = (distance_m, speed_m_s)
        raise UnsupportedCapabilityError("Relative movement is not implemented")

    async def turn_relative(self, angle_rad: float, speed_rad_s: float) -> None:
        """逆时针为正旋转；实现必须支持取消，返回不代替到位验证."""
        _ = (angle_rad, speed_rad_s)
        raise UnsupportedCapabilityError("Relative turning is not implemented")

    async def navigate_to(self, goal: NavigationGoal) -> None:
        """在指定地图坐标系导航；由设备导航系统负责路径规划和避障."""
        _ = goal
        raise UnsupportedCapabilityError("Navigation is not implemented")

    @abstractmethod
    async def stop(self) -> None:
        """请求停止；调用方需读取设备状态，确认停止已经发生."""
        pass
