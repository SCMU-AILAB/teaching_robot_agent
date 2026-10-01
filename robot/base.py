"""设备边界：只描述设备能力，不管理任务和动作队列。"""

from abc import ABC, abstractmethod

from domain.models import RobotState


class RobotAdapter(ABC):
    is_simulated: bool = False

    @abstractmethod
    async def connect(self) -> None:
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        pass

    @abstractmethod
    async def get_state(self) -> RobotState:
        pass

    @abstractmethod
    async def move_relative(self, distance_m: float, speed_m_s: float) -> None:
        """沿当前朝向相对移动；返回后仍由 Skill 验证实际位姿。"""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """请求停止；调用方需读取设备状态，确认停止已经发生。"""
        pass
