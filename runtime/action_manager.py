"""单执行器 Runtime：提交立即返回，后台执行，终态在清理之后发布。"""

import asyncio
import time
from copy import deepcopy
from types import TracebackType

from domain.models import (
    ActionEvent,
    ActionRecord,
    ActionRequest,
    ActionStatus,
    SkillResult,
)
from domain.validation import (
    finite_float,
    nonempty_string,
    positive_int,
    string_key_dict,
)
from robot.base import RobotAdapter
from skills.base import RobotSkill
from skills.registry import SkillRegistry
from storage.memory import InMemoryStore

_TRANSITIONS: dict[ActionStatus, set[ActionStatus]] = {
    ActionStatus.QUEUED: {
        ActionStatus.RUNNING,
        ActionStatus.CANCELLED,
        ActionStatus.FAILED,
    },
    ActionStatus.RUNNING: {
        ActionStatus.VERIFYING,
        ActionStatus.CANCELLING,
        ActionStatus.FAILED,
        ActionStatus.TIMED_OUT,
    },
    ActionStatus.VERIFYING: {
        ActionStatus.SUCCEEDED,
        ActionStatus.CANCELLING,
        ActionStatus.FAILED,
        ActionStatus.TIMED_OUT,
    },
    ActionStatus.CANCELLING: {
        ActionStatus.CANCELLED,
        ActionStatus.FAILED,
        ActionStatus.TIMED_OUT,
    },
}


class ActionManager:
    """所有方法在同一个 asyncio 事件循环调用；首版所有动作共用一条队列。"""

    def __init__(
        self,
        robot: RobotAdapter,
        registry: SkillRegistry,
        store: InMemoryStore | None = None,
        cleanup_timeout_s: float = 1.0,
    ) -> None:
        self.robot: RobotAdapter = robot
        self.registry: SkillRegistry = registry
        self.store: InMemoryStore = store if store is not None else InMemoryStore()
        self.cleanup_timeout_s: float = self._validate_timeout(cleanup_timeout_s)
        self._queue: asyncio.Queue[int | None] = asyncio.Queue()
        self._events: asyncio.Queue[ActionEvent] = asyncio.Queue()
        self._done: dict[int, asyncio.Event] = {}
        self._worker: asyncio.Task[None] | None = None
        self._active_action_id: int | None = None
        self._execution: asyncio.Task[SkillResult] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()
        self._started: bool = False
        self._closing: bool = False
        self._closed: bool = False
        self._blocked_reason: str | None = None

    @property
    def blocked_reason(self) -> str | None:
        return self._blocked_reason

    async def __aenter__(self) -> "ActionManager":
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        # 连接可能需要等待；与 close 共用锁，避免关闭后又创建执行器。
        async with self._lifecycle_lock:
            if self._closed or self._closing:
                raise RuntimeError("A closed runtime cannot be restarted")
            if self._started:
                return
            try:
                await self.robot.connect()
            except BaseException:
                # 连接失败或调用方取消，也需要清理可能已建立的部分连接。
                await self.robot.disconnect()
                raise
            self._started = True
            self._worker = asyncio.create_task(self._run(), name="robot-action-worker")

    @staticmethod
    def _validate_timeout(value: object) -> float:
        timeout = finite_float(value, "timeout_s")
        if timeout <= 0:
            raise ValueError("Timeout must be a finite positive number of seconds")
        return timeout

    @classmethod
    def _validate_request(cls, request: object) -> ActionRequest:
        """在运行时输入边界校验，再把明确类型的请求交给执行层。"""
        if not isinstance(request, ActionRequest):
            raise ValueError("Expected an ActionRequest")
        return ActionRequest(
            task_id=positive_int(request.task_id, "task_id"),
            skill_name=nonempty_string(request.skill_name, "skill_name"),
            args=deepcopy(string_key_dict(request.args, "args")),
            timeout_s=cls._validate_timeout(request.timeout_s),
        )

    async def submit_action(self, request: ActionRequest) -> ActionRecord:
        if not self._started or self._closing or self._closed:
            raise RuntimeError("Start the runtime before submitting actions")
        if self._blocked_reason is not None:
            raise RuntimeError(f"Runtime blocked: {self._blocked_reason}")
        request = self._validate_request(request)
        skill = self.registry.get(request.skill_name)
        skill.validate(request.args)
        record = self.store.create_action(request)
        self._done[record.action_id] = asyncio.Event()
        self._publish(record)
        self._queue.put_nowait(record.action_id)
        return record

    def get_action(self, action_id: int) -> ActionRecord:
        return self.store.get_action(action_id)

    def list_actions(self, task_id: int | None = None) -> list[ActionRecord]:
        return self.store.list_actions(task_id)

    async def wait_for_action(self, action_id: int) -> ActionRecord:
        record = self.get_action(action_id)
        if not record.status.is_terminal:
            # 只等待通知；取消这个等待者不会取消后台执行的动作。
            _ = await self._done[action_id].wait()
        return self.get_action(action_id)

    async def next_event(self) -> ActionEvent:
        """单消费者事件流，供协调器或控制台接收，无需轮询动作状态。"""
        return await self._events.get()

    async def cancel_action(self, action_id: int) -> ActionRecord:
        record = self.get_action(action_id)
        if record.status.is_terminal or record.status == ActionStatus.CANCELLING:
            return record
        if record.status == ActionStatus.QUEUED:
            return self._transition(action_id, ActionStatus.CANCELLED)
        record = self._transition(action_id, ActionStatus.CANCELLING)
        if self._active_action_id == action_id and self._execution is not None:
            _ = self._execution.cancel()
        return record

    def _publish(self, record: ActionRecord) -> None:
        self._events.put_nowait(
            ActionEvent(
                action_id=record.action_id,
                task_id=record.raw_request.task_id,
                status=record.status,
            )
        )
        if record.status.is_terminal:
            self._done[record.action_id].set()

    def _transition(
        self,
        action_id: int,
        status: ActionStatus,
        *,
        result: SkillResult | None = None,
        failure: str | None = None,
    ) -> ActionRecord:
        record = self.get_action(action_id)
        if status not in _TRANSITIONS.get(record.status, set()):
            raise RuntimeError(f"Invalid transition: {record.status} -> {status}")
        record.status = status
        if status == ActionStatus.RUNNING:
            record.started_at = time.time()
        if status.is_terminal:
            record.ended_at = time.time()
            record.result = result
            record.failure = failure
        self.store.save_action(record)
        self._publish(record)
        return record

    async def _run(self) -> None:
        while True:
            action_id = await self._queue.get()
            try:
                if action_id is None:
                    return
                record = self.get_action(action_id)
                if record.status.is_terminal:
                    continue
                if self._blocked_reason is not None:
                    _ = self._transition(
                        action_id,
                        ActionStatus.FAILED,
                        failure=f"Runtime blocked: {self._blocked_reason}",
                    )
                    continue
                await self._execute(record)
            finally:
                self._queue.task_done()

    async def _perform(self, record: ActionRecord, skill: RobotSkill) -> SkillResult:
        await skill.check_preconditions(self.robot)
        result = await skill.execute(self.robot, deepcopy(record.raw_request.args))
        if self.get_action(record.action_id).status == ActionStatus.CANCELLING:
            raise asyncio.CancelledError
        _ = self._transition(record.action_id, ActionStatus.VERIFYING)
        if not await skill.verify(self.robot, result):
            raise RuntimeError("Skill completion verification failed")
        return result

    async def _cleanup(self, skill: RobotSkill) -> None:
        await skill.cleanup(self.robot)
        state = await self.robot.get_state()
        if not state.is_connected or state.is_moving:
            raise RuntimeError("Robot stop could not be confirmed")

    async def _execute(self, record: ActionRecord) -> None:
        skill = self.registry.get(record.raw_request.skill_name)
        self._active_action_id = record.action_id
        _ = self._transition(record.action_id, ActionStatus.RUNNING)
        self._execution = asyncio.create_task(
            self._perform(record, skill), name=f"robot-action-{record.action_id}"
        )
        outcome = ActionStatus.SUCCEEDED
        result: SkillResult | None = None
        failure: str | None = None
        deadline = asyncio.timeout(record.raw_request.timeout_s)
        try:
            # asyncio 的超时使用单调时钟；记录中的时间戳用于日志展示。
            async with deadline:
                result = await self._execution
        except TimeoutError as exc:
            if deadline.expired():
                outcome = ActionStatus.TIMED_OUT
                failure = f"Action exceeded {record.raw_request.timeout_s:g} s timeout"
            else:
                outcome = ActionStatus.FAILED
                failure = f"TimeoutError: {exc}"
        except asyncio.CancelledError:
            if self.get_action(record.action_id).status == ActionStatus.CANCELLING:
                outcome = ActionStatus.CANCELLED
            else:
                outcome = ActionStatus.FAILED
                failure = "Skill cancelled itself unexpectedly"
        except Exception as exc:
            outcome = ActionStatus.FAILED
            failure = f"{type(exc).__name__}: {exc}"

        try:
            # 取消执行不等于物理停止。完成清理后，才释放执行器处理下一动作。
            await asyncio.wait_for(self._cleanup(skill), timeout=self.cleanup_timeout_s)
        except Exception as exc:
            detail = f"Stop/cleanup failed: {type(exc).__name__}: {exc}"
            self._blocked_reason = detail
            failure = f"{failure}; {detail}" if failure else detail
            outcome = ActionStatus.FAILED

        if (
            outcome == ActionStatus.SUCCEEDED
            and self.get_action(record.action_id).status == ActionStatus.CANCELLING
        ):
            outcome = ActionStatus.CANCELLED
        _ = self._transition(
            record.action_id,
            outcome,
            result=result if outcome == ActionStatus.SUCCEEDED else None,
            failure=failure,
        )
        self._active_action_id = None
        self._execution = None

    async def close(self) -> None:
        """停止接收动作，取消未完成动作，等待清理，再断开设备。"""
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(
                self._close(), name="robot-runtime-close"
            )
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        async with self._lifecycle_lock:
            try:
                if not self._started:
                    return
                for record in self.list_actions():
                    if not record.status.is_terminal:
                        _ = await self.cancel_action(record.action_id)
                self._queue.put_nowait(None)
                try:
                    if self._worker is not None:
                        await self._worker
                finally:
                    await self.robot.disconnect()
            finally:
                self._started = False
                self._closed = True
