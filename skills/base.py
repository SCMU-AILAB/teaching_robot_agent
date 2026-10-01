# skills/base.py
"""Skill 负责动作的语义、校验与完成证据，Runtime 负责生命周期."""

from abc import ABC, abstractmethod

from domain.models import SkillResult
from robot.base import RobotAdapter


class RobotSkill(ABC):
    """动作语义契约，定义校验、执行、验证和清理流程."""

    name: str

    @abstractmethod
    def validate(self, args: dict[str, object]) -> None:
        """检查动作参数，非法输入抛出异常."""
        pass

    async def check_preconditions(self, robot: RobotAdapter) -> None:
        """确认设备已连接且未处于运动状态."""
        state = await robot.get_state()
        if not state.is_connected:
            raise RuntimeError("robot is not connected")
        if state.is_moving:
            raise RuntimeError("robot is already moving")

    @abstractmethod
    async def execute(
        self, robot: RobotAdapter, args: dict[str, object]
    ) -> SkillResult:
        """执行动作并收集证据，完成判定由验证阶段负责."""
        pass

    @abstractmethod
    async def verify(self, robot: RobotAdapter, result: SkillResult) -> bool:
        """结合设备反馈和结果证据判断动作是否完成."""
        pass

    async def cleanup(self, robot: RobotAdapter) -> None:
        """请求停止并确认连接与静止状态，失败时抛出异常."""
        await robot.stop()
        state = await robot.get_state()
        if not state.is_connected or state.is_moving:
            raise RuntimeError("could not confirm that the connected robot stopped")
