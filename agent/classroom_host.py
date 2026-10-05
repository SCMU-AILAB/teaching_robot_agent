# agent/classroom_host.py
"""串联教学输入、模型追问和动作终态的单消费者课堂宿主."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from agent.embodied_agent import AgentTurn
from agent.teaching_flow import TeachingFlow
from app.team_gateway import TeamGateway, UserInput
from domain.education import TeachingSession, TeachingStage
from domain.models import ActionStatus, TaskState, TaskStatus
from domain.services import ObservationResult
from domain.validation import positive_int
from education.service import EducationService


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


@dataclass(frozen=True)
class ClassroomReply:
    """课堂输出与提交时的状态，动作编号仅记录已等待的实际动作."""

    task_id: int
    text: str
    teaching: TeachingSession
    task_status: TaskStatus
    action_ids: tuple[int, ...] = ()
    rejected: bool = False


class ClassroomHost:
    """回答由规则推进，追问由模型处理；停止绕过消费锁."""

    def __init__(
        self,
        _gateway: TeamGateway,
        _education: EducationService,
        _agent_factory: Callable[[int], DecisionAgent],
        _max_actions: int = 3,
    ) -> None:
        """注入共享服务，采用串行输入与有限动作续接策略."""
        self._gateway: TeamGateway = _gateway
        self._education: EducationService = _education
        self._factory: Callable[[int], DecisionAgent] = _agent_factory
        self._flow: TeachingFlow = TeachingFlow(_gateway, _education)
        self._max_actions: int = positive_int(_max_actions, "max_actions")
        self._lock: asyncio.Lock = asyncio.Lock()
        self._agents: dict[int, DecisionAgent] = {}
        self._seen: dict[str, UserInput] = {}
        self._replies: dict[str, ClassroomReply] = {}
        self._workers: dict[int, asyncio.Task[ClassroomReply]] = {}
        self._stopped: set[int] = set()
        self._closed: bool = False

    async def start(self, task_id: int) -> ClassroomReply:
        """开始讲解并生成带编号的问题，不创建后台输入消费者."""
        async with self._lock:
            await self._require_active(task_id)
            reply = await self._flow.start(task_id)
            if task_id not in self._agents:
                self._agents[task_id] = self._factory(task_id)
            return await self._reply(task_id, reply.text)

    async def process_next(self) -> ClassroomReply:
        """由应用唯一消费者循环调用，等待动作期间后续输入保留在队列."""
        return await self.handle(await self._gateway.next_input())

    async def handle(self, item: UserInput) -> ClassroomReply:
        """处理一条输入并等待其动作链结束，成功重试返回原结果.

        Args:
            item: 入口生成的文字或最终语音输入，答案须带原问题编号。

        Returns:
            教学状态及最终输出；不输出动作接受时的模型完成声明。

        Raises:
            ValueError: 未启动、输入冲突、任务停止或失败输入被重放。
            RuntimeError: 模型重复动作或超出续接上限。
            Exception: 模型或工具失败；宿主先取消任务动作再向外传播。
        """
        async with self._lock:
            # ========== Step1: 去重在副作用之前完成，失败输入不自动重放 ==========
            previous = self._seen.get(item.input_id)
            if previous is not None:
                if previous != item:
                    raise ValueError("Input ID reused with different content")
                if item.input_id in self._replies:
                    return self._replies[item.input_id]
                raise ValueError("Previous processing failed; do not replay input")
            await self._require_active(item.task_id)
            if item.task_id not in self._agents:
                raise ValueError("Start the classroom before handling input")
            self._seen[item.input_id] = item
            worker = asyncio.create_task(self._handle(item), name="classroom-input")
            self._workers[item.task_id] = worker
            try:
                reply = await worker
                self._replies[item.input_id] = reply
                return reply
            except BaseException:
                # 输入处理被取消也不能遗留已接受的机器人动作。
                self._stopped.add(item.task_id)
                _ = await self._gateway.cancel_task(item.task_id)
                raise
            finally:
                _ = self._workers.pop(item.task_id, None)

    async def cancel(self, task_id: int) -> TaskState:
        """停止不等待消费锁或模型返回，执行层负责确认设备停止."""
        self._stopped.add(task_id)
        worker = self._workers.get(task_id)
        if worker is not None:
            _ = worker.cancel()
        return await self._gateway.cancel_task(task_id)

    async def close(self) -> None:
        """结束所有已启动课堂并等待本宿主的工作协程释放."""
        self._closed = True
        workers = tuple(self._workers.values())
        for task_id in tuple(self._agents):
            _ = await self.cancel(task_id)
        if workers:
            _ = await asyncio.gather(*workers, return_exceptions=True)

    async def _require_active(self, task_id: int) -> None:
        """阻止停止之后的新教学及迟到模型输出."""
        snapshot = await self._gateway.get_snapshot(task_id)
        if (
            self._closed
            or task_id in self._stopped
            or snapshot.task.status
            not in {
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
            }
        ):
            raise ValueError("Classroom is no longer active")

    async def _reply(
        self,
        task_id: int,
        text: str,
        action_ids: tuple[int, ...] = (),
        rejected: bool = False,
    ) -> ClassroomReply:
        """汇总本轮处理之后的教学与执行状态."""
        snapshot = await self._gateway.get_snapshot(task_id)
        return ClassroomReply(
            task_id,
            text,
            self._education.get_state(task_id),
            snapshot.task.status,
            action_ids,
            rejected,
        )

    async def _handle(self, item: UserInput) -> ClassroomReply:
        """答案不依赖模型评分；追问完成动作后按最新快照续接模型."""
        if item.question_id is not None:
            reply = await self._flow.handle(item)
            if reply.state.stage == TeachingStage.COMPLETED:
                _ = self._gateway.complete_teaching_task(reply.state)
            return await self._reply(item.task_id, reply.text, rejected=reply.rejected)
        # ========== Step2: 追问保留教学问题，动作终态触发下一次决策 ==========
        agent = self._agents[item.task_id]
        turn = await agent.decide(item)
        actions: list[int] = []
        while True:
            await self._require_active(item.task_id)
            action_id = turn.pending_action_id
            if action_id is None:
                return await self._reply(item.task_id, turn.text, tuple(actions))
            if action_id in actions or len(actions) >= self._max_actions:
                raise RuntimeError("Repeated action or classroom action limit exceeded")
            actions.append(action_id)
            result = await self._gateway.wait_for_action(item.task_id, action_id)
            await self._require_active(item.task_id)
            if result.status != ActionStatus.SUCCEEDED:
                self._stopped.add(item.task_id)
                _ = await self._gateway.cancel_task(item.task_id)
                return await self._reply(
                    item.task_id,
                    f"动作未成功，状态为 {result.status.value}，已结束本次课堂。",
                    tuple(actions),
                )
            if len(actions) == self._max_actions:
                return await self._reply(
                    item.task_id,
                    "本轮动作已验证完成，已达到连续动作上限；后续操作请重新提出。",
                    tuple(actions),
                )
            # 不重发原输入，避免模型把相同移动指令再次当作新命令。
            turn = await agent.decide()
