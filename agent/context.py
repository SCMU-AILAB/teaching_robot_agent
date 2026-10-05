# agent/context.py
"""统一教学、环境与执行上下文，不触发外部模型调用."""

import time
from dataclasses import dataclass, replace
from typing import Protocol

from app.team_gateway import TaskSnapshot, TeamGateway, UserInput
from domain.education import TeachingSession
from domain.services import ObservationResult
from education.service import EducationService


@dataclass(frozen=True)
class TeachingContext:
    """本轮输入与各模块状态，过期观察显式标记."""

    task: TaskSnapshot
    teaching: TeachingSession
    user_input: UserInput | None
    observation: ObservationResult | None


@dataclass(frozen=True)
class RobotContext:
    """家庭机器人统一上下文，不以教学会话作为任务前提."""

    task: TaskSnapshot
    user_input: UserInput | None
    observation: ObservationResult | None


class ContextProvider(Protocol):
    """模型依赖统一读取契约，允许课程模式提供附加状态."""

    async def build(
        self,
        task_id: int,
        user_input: UserInput | None = None,
        observation: ObservationResult | None = None,
    ) -> RobotContext | TeachingContext:
        """读取本轮实际状态."""
        ...


class RobotContextBuilder:
    """汇总任务、机器人能力及最近观察，不自动开课."""

    def __init__(self, _gateway: TeamGateway) -> None:
        """注入统一业务门面."""
        self._gateway: TeamGateway = _gateway

    async def build(
        self,
        task_id: int,
        user_input: UserInput | None = None,
        observation: ObservationResult | None = None,
    ) -> RobotContext:
        """读取最近观察，移动后的历史画面不得当作当前现场."""
        if user_input is not None and user_input.task_id != task_id:
            raise ValueError("Input belongs to another task")
        observation = observation or self._gateway.latest_observation(task_id)
        if observation is not None:
            if observation.task_id != task_id:
                raise ValueError("Observation belongs to another task")
            observation = replace(
                observation,
                stale=observation.stale
                or time.time() - observation.frame.captured_at > 2,
            )
        return RobotContext(
            await self._gateway.get_snapshot(task_id), user_input, observation
        )


class ContextBuilder:
    """每次决策前读取最新任务与教学状态."""

    def __init__(self, _gateway: TeamGateway, _education: EducationService) -> None:
        """注入核心入口与教学服务."""
        self._gateway: TeamGateway = _gateway
        self._education: EducationService = _education

    async def build(
        self,
        task_id: int,
        user_input: UserInput | None = None,
        observation: ObservationResult | None = None,
    ) -> TeachingContext:
        """校验来源，读取最新状态并标记超过两秒的观察."""
        if user_input is not None and user_input.task_id != task_id:
            raise ValueError("Input belongs to another task")
        if observation is not None and observation.task_id != task_id:
            raise ValueError("Observation belongs to another task")
        snapshot = await self._gateway.get_snapshot(task_id)
        if observation is not None:
            observation = replace(
                observation,
                stale=observation.stale
                or time.time() - observation.frame.captured_at > 2.0,
            )
        return TeachingContext(
            snapshot, self._education.get_state(task_id), user_input, observation
        )
