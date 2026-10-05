# agent/interfaces.py
"""任务宿主与模型策略之间的通用决策契约."""

from typing import Protocol

from agent.embodied_agent import AgentTurn
from app.team_gateway import UserInput
from domain.services import ObservationResult


class DecisionAgent(Protocol):
    """宿主依赖决策契约，测试与离线演示可注入替代实现."""

    async def decide(
        self,
        user_input: UserInput | None = None,
        observation: ObservationResult | None = None,
        timeout_s: float = 90,
    ) -> AgentTurn:
        """返回讲解或待执行动作编号."""
        ...
