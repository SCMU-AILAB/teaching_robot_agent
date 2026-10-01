"""Skill 负责动作的语义、校验与完成证据，Runtime 负责生命周期。"""

from abc import ABC, abstractmethod

from domain.models import SkillResult
from robot.base import RobotAdapter


class RobotSkill(ABC):
    name: str

    @abstractmethod
    def validate(self, args: dict[str, object]) -> None:
        pass

    async def check_preconditions(self, robot: RobotAdapter) -> None:
        state = await robot.get_state()
        if not state.is_connected:
            raise RuntimeError("robot is not connected")
        if state.is_moving:
            raise RuntimeError("robot is already moving")

    @abstractmethod
    async def execute(
        self, robot: RobotAdapter, args: dict[str, object]
    ) -> SkillResult:
        pass

    @abstractmethod
    async def verify(self, robot: RobotAdapter, result: SkillResult) -> bool:
        pass

    async def cleanup(self, robot: RobotAdapter) -> None:
        await robot.stop()
        state = await robot.get_state()
        if not state.is_connected or state.is_moving:
            raise RuntimeError("could not confirm that the connected robot stopped")
