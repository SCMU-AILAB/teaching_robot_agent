# app/team_gateway.py
"""队友可直接调用的核心门面，复用现有任务与动作运行时."""

import asyncio
import time
from dataclasses import dataclass, replace
from uuid import uuid4

from domain.education import TeachingSession
from domain.models import ActionRecord, RobotState, TaskState, TaskStatus
from domain.robot import RobotCapability
from domain.services import ObservationRequest, ObservationResult, TranscriptResult
from domain.validation import nonempty_string
from perception.interfaces import PerceptionService
from runtime.action_manager import ActionManager
from runtime.task_coordinator import TaskCoordinator
from runtime.task_resources import TaskResource


@dataclass(frozen=True)
class TaskSnapshot:
    """展示模块读取的任务、动作与机器人快照."""

    task: TaskState
    actions: tuple[ActionRecord, ...]
    robot: RobotState
    blocked_reason: str | None
    capabilities: tuple[RobotCapability, ...] = ()
    available_skills: tuple[str, ...] = ()
    skill_parameters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class UserInput:
    """供后续教学模块消费的统一文字或最终语音输入."""

    input_id: str
    task_id: int
    text: str
    source: str
    recording_id: str | None = None
    question_id: str | None = None


class TeamGateway:
    """提供真实任务查询、取消、观察及输入队列；不伪装已实现教学 Agent."""

    def __init__(self, _runtime: ActionManager, _perception: PerceptionService) -> None:
        """注入核心运行时与队友提供的感知实现."""
        self._runtime: ActionManager = _runtime
        self._coordinator: TaskCoordinator = TaskCoordinator(_runtime)
        self._perception: PerceptionService = _perception
        self._inputs: asyncio.Queue[UserInput] = asyncio.Queue(1024)
        self._transcripts: dict[tuple[int, str], UserInput] = {}
        self._scene_generation: int = 0
        self._observations: dict[int, ObservationResult] = {}

    def register_task_resource(self, resource: TaskResource) -> None:
        """应用装配附属资源，使统一任务停止等待资源确认."""
        self._coordinator.register_resource(resource)

    def require_active(self, task_id: int) -> None:
        """同步校验任务，资源启动和副作用前使用以关闭竞态窗口."""
        self._require_active(task_id)

    def latest_observation(self, task_id: int) -> ObservationResult | None:
        """提供最近观察证据，不保证新鲜度，调用方必须检查 stale 和时间."""
        return self._observations.get(task_id)

    def resolve_observation(
        self, task_id: int, supplied: ObservationResult | None
    ) -> ObservationResult | None:
        """只信任门面登记的最新观察，显式旧对象不能覆盖版本失效."""
        if supplied is not None and supplied.task_id != task_id:
            raise ValueError("Observation belongs to another task")
        latest = self.latest_observation(task_id)
        if latest is None:
            return replace(supplied, stale=True) if supplied is not None else None
        age = time.time() - latest.frame.captured_at
        return replace(latest, stale=latest.stale or age < 0 or age > 2)

    def finish_interaction(self, task_id: int) -> TaskState:
        """由宿主显式结束一次问答任务，仍检查所有实际动作终态."""
        return self._coordinator.complete_task(task_id, interaction_completed=True)

    def create_task(self, user_target: str) -> TaskState:
        """创建任务记录，后续教学决策由独立消费者负责."""
        return self._coordinator.create_task(user_target)

    def _require_active(self, task_id: int) -> None:
        """拒绝向取消中或已终结任务提交迟到结果."""
        task = self._coordinator.get_task(task_id)
        if task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            raise ValueError("Task no longer accepts input")

    async def get_snapshot(self, task_id: int) -> TaskSnapshot:
        """读取用于本地展示的快照，不表示 HTTP 事件游标快照."""
        robot = await self._runtime.robot.get_state()
        return TaskSnapshot(
            self._coordinator.get_task(task_id),
            tuple(self._runtime.list_actions(task_id)),
            robot,
            self._runtime.blocked_reason,
            tuple(sorted(self._runtime.robot.capabilities)),
            tuple(self._runtime.registry.names()),
            tuple(
                (name, self._runtime.registry.get(name).parameter_help)
                for name in self._runtime.registry.names()
            ),
        )

    async def cancel_task(self, task_id: int) -> TaskState:
        """直接取消任务动作，绕过任何模型推理."""
        return await self._coordinator.cancel_task(task_id)

    def complete_teaching_task(self, session: TeachingSession) -> TaskState:
        """根据已完成教学快照终结任务，仍要求所有实际动作结束."""
        return self._coordinator.complete_task(session.task_id, teaching=session)

    async def submit_action(
        self,
        task_id: int,
        skill_name: str,
        args: dict[str, object],
        timeout_s: float = 10,
    ) -> ActionRecord:
        """供核心决策代码提交动作，不是公开网络控制端点."""
        record = await self._coordinator.submit_action(
            task_id, skill_name, args, timeout_s
        )
        if skill_name in {"move_relative", "turn_relative", "navigate_to"}:
            self._scene_generation += 1
            self._observations = {
                key: replace(value, stale=True)
                for key, value in self._observations.items()
            }
            _ = self._perception.invalidate("提交相对移动，场景可能变化")
        return record

    def get_action_status(self, task_id: int, action_id: int) -> ActionRecord:
        """检查动作归属后返回记录，禁止跨任务查询动作."""
        record = self._runtime.get_action(action_id)
        if record.raw_request.task_id != task_id:
            raise KeyError("Action does not belong to task")
        return record

    async def cancel_action(self, task_id: int, action_id: int) -> ActionRecord:
        """只取消当前任务拥有的动作，不结束教学任务."""
        _ = self.get_action_status(task_id, action_id)
        return await self._runtime.cancel_action(action_id)

    async def wait_for_action(self, task_id: int, action_id: int) -> ActionRecord:
        """事件驱动等待终态，不轮询模型或设备."""
        _ = self.get_action_status(task_id, action_id)
        result = await self._runtime.wait_for_action(action_id)
        if result.raw_request.skill_name in {
            "move_relative",
            "turn_relative",
            "navigate_to",
        }:
            self._scene_generation += 1
            self._observations = {
                key: replace(value, stale=True)
                for key, value in self._observations.items()
            }
            _ = self._perception.invalidate(
                "运动已结束，下一次观察必须使用新的场景版本"
            )
        return result

    def submit_text(
        self, task_id: int, text: str, question_id: str | None = None
    ) -> UserInput:
        """接受文字并放入统一输入队列."""
        self._require_active(task_id)
        item = UserInput(
            uuid4().hex,
            task_id,
            nonempty_string(text, "text"),
            "text",
            question_id=question_id,
        )
        self._inputs.put_nowait(item)
        return item

    def submit_transcript(
        self,
        task_id: int,
        recording_id: str,
        transcript: TranscriptResult,
        question_id: str | None = None,
    ) -> UserInput | None:
        """最终语音按录音编号去重，空文本与中间结果不提交."""
        self._require_active(task_id)
        recording_id = nonempty_string(recording_id, "recording_id")
        if not transcript.is_final or not transcript.text.strip():
            return None
        key = (task_id, recording_id)
        previous = self._transcripts.get(key)
        if previous is not None:
            if previous.text != transcript.text or previous.question_id != question_id:
                raise ValueError("Recording already submitted with different text")
            return previous
        if len(self._transcripts) >= 4096:
            raise RuntimeError("Transcript retention capacity reached")
        item = UserInput(
            uuid4().hex, task_id, transcript.text, "speech", recording_id, question_id
        )
        self._inputs.put_nowait(item)
        self._transcripts[key] = item
        return item

    async def next_input(self, *, include_terminal: bool = False) -> UserInput:
        """单个教学消费者读取输入，跳过已经取消任务的排队内容."""
        while True:
            item = await self._inputs.get()
            self._inputs.task_done()
            if include_terminal:
                return item
            try:
                self._require_active(item.task_id)
            except ValueError:
                continue
            return item

    async def observe(self, request: ObservationRequest) -> ObservationResult:
        """调用感知服务，任务取消后拒绝迟到观察进入教学流程."""
        self._require_active(request.task_id)
        generation = self._scene_generation
        async with asyncio.timeout(request.timeout_s):
            result = await self._perception.observe(request)
        self._require_active(request.task_id)
        if (
            result.task_id != request.task_id
            or result.observation_id != request.observation_id
        ):
            raise ValueError("Perception result does not match request")
        moving = any(
            record.raw_request.skill_name
            in {"move_relative", "turn_relative", "navigate_to"}
            and not record.status.is_terminal
            for record in self._runtime.list_actions()
        )
        result = replace(
            result, stale=result.stale or moving or generation != self._scene_generation
        )
        self._observations[request.task_id] = result
        return result
