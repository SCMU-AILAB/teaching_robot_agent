# robot/base.py
"""设备边界：只描述设备能力，不管理任务和动作队列."""

from abc import ABC, abstractmethod

from domain.models import RobotState


class RobotAdapter(ABC):
    """设备能力契约，具体适配器负责连接与停止确认."""

    is_simulated: bool = False

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

    @abstractmethod
    async def move_relative(self, distance_m: float, speed_m_s: float) -> None:
        """沿当前朝向相对移动；返回后仍由 Skill 验证实际位姿."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """请求停止；调用方需读取设备状态，确认停止已经发生."""
        pass
