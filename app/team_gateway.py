# app/team_gateway.py
"""队友可直接调用的核心门面，复用现有任务与动作运行时."""

import asyncio
from dataclasses import dataclass
from uuid import uuid4

from domain.models import ActionRecord, RobotState, TaskState, TaskStatus
from domain.services import ObservationRequest, ObservationResult, TranscriptResult
from domain.validation import nonempty_string
from perception.interfaces import PerceptionService
from runtime.action_manager import ActionManager
from runtime.task_coordinator import TaskCoordinator


@dataclass(frozen=True)
class TaskSnapshot:
    """展示模块读取的任务、动作与机器人快照."""

    task: TaskState
    actions: tuple[ActionRecord, ...]
    robot: RobotState
    blocked_reason: str | None


@dataclass(frozen=True)
class UserInput:
    """供后续教学模块消费的统一文字或最终语音输入."""

    input_id: str
    task_id: int
    text: str
    source: str
    recording_id: str | None = None


class TeamGateway:
    """提供真实任务查询、取消、观察及输入队列；不伪装已实现教学 Agent."""

    def __init__(self, _runtime: ActionManager, _perception: PerceptionService) -> None:
        """注入核心运行时与队友提供的感知实现."""
        self._runtime: ActionManager = _runtime
        self._coordinator: TaskCoordinator = TaskCoordinator(_runtime)
        self._perception: PerceptionService = _perception
        self._inputs: asyncio.Queue[UserInput] = asyncio.Queue()
        self._transcripts: dict[tuple[int, str], UserInput] = {}

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
        )

    async def cancel_task(self, task_id: int) -> TaskState:
        """直接取消任务动作，绕过任何模型推理."""
        return await self._coordinator.cancel_task(task_id)

    async def submit_action(
        self,
        task_id: int,
        skill_name: str,
        args: dict[str, object],
        timeout_s: float = 10,
    ) -> ActionRecord:
        """供核心决策代码提交动作，不是公开网络控制端点."""
        return await self._coordinator.submit_action(
            task_id, skill_name, args, timeout_s
        )

    def submit_text(self, task_id: int, text: str) -> UserInput:
        """接受文字并放入统一输入队列."""
        self._require_active(task_id)
        item = UserInput(uuid4().hex, task_id, nonempty_string(text, "text"), "text")
        self._inputs.put_nowait(item)
        return item

    def submit_transcript(
        self, task_id: int, recording_id: str, transcript: TranscriptResult
    ) -> UserInput | None:
        """最终语音按录音编号去重，空文本与中间结果不提交."""
        self._require_active(task_id)
        recording_id = nonempty_string(recording_id, "recording_id")
        if not transcript.is_final or not transcript.text.strip():
            return None
        key = (task_id, recording_id)
        previous = self._transcripts.get(key)
        if previous is not None:
            if previous.text != transcript.text:
                raise ValueError("Recording already submitted with different text")
            return previous
        item = UserInput(uuid4().hex, task_id, transcript.text, "speech", recording_id)
        self._transcripts[key] = item
        self._inputs.put_nowait(item)
        return item

    async def next_input(self) -> UserInput:
        """单个教学消费者读取输入，跳过已经取消任务的排队内容."""
        while True:
            item = await self._inputs.get()
            self._inputs.task_done()
            try:
                self._require_active(item.task_id)
            except ValueError:
                continue
            return item

    async def observe(self, request: ObservationRequest) -> ObservationResult:
        """调用感知服务，任务取消后拒绝迟到观察进入教学流程."""
        self._require_active(request.task_id)
        async with asyncio.timeout(request.timeout_s):
            result = await self._perception.observe(request)
        self._require_active(request.task_id)
        if (
            result.task_id != request.task_id
            or result.observation_id != request.observation_id
        ):
            raise ValueError("Perception result does not match request")
        return result
