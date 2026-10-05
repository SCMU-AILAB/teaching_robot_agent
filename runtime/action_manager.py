# runtime/action_manager.py
"""单执行器 Runtime：提交立即返回，后台执行，终态在清理之后发布."""

import asyncio
import logging
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

logger = logging.getLogger(__name__)

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
    """所有方法在同一个 asyncio 事件循环调用；首版所有动作共用一条队列."""

    def __init__(
        self,
        _robot: RobotAdapter,
        _registry: SkillRegistry,
        _store: InMemoryStore | None = None,
        _cleanup_timeout_s: float = 1.0,
    ) -> None:
        """初始化动作运行时，连接和执行器由启动方法建立.

        Args:
            _robot: 用于执行动作和确认停止的设备适配器。
            _registry: 可用动作的注册表。
            _store: 共享记录存储，省略时创建内存存储。
            _cleanup_timeout_s: 清理与停止确认的期限，单位为秒。

        Raises:
            ValueError: 清理期限不是有限正数。
        """
        self.robot: RobotAdapter = _robot
        self.registry: SkillRegistry = _registry
        self.store: InMemoryStore = _store if _store is not None else InMemoryStore()
        self.cleanup_timeout_s: float = self._validate_timeout(_cleanup_timeout_s)
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
        """返回禁止继续执行动作的原因."""
        return self._blocked_reason

    async def __aenter__(self) -> "ActionManager":
        """启动运行时并返回当前实例."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """退出上下文时等待动作清理并关闭设备."""
        await self.close()

    async def start(self) -> None:
        # 连接可能需要等待；与 close 共用锁，避免关闭后又创建执行器。
        """串行化启动与关闭，连接失败时清理部分连接."""
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
            logger.info("[ActionManager.start] 设备已连接，动作执行器已启动")

    @staticmethod
    def _validate_timeout(value: object) -> float:
        """校验执行期限为有限正数，单位为秒."""
        timeout = finite_float(value, "timeout_s")
        if timeout <= 0:
            raise ValueError("Timeout must be a finite positive number of seconds")
        return timeout

    @classmethod
    def _validate_request(cls, request: object) -> ActionRequest:
        """在运行时输入边界校验，再把明确类型的请求交给执行层."""
        if not isinstance(request, ActionRequest):
            raise ValueError("Expected an ActionRequest")
        return ActionRequest(
            task_id=positive_int(request.task_id, "task_id"),
            skill_name=nonempty_string(request.skill_name, "skill_name"),
            args=deepcopy(string_key_dict(request.args, "args")),
            timeout_s=cls._validate_timeout(request.timeout_s),
        )

    async def submit_action(self, request: ActionRequest) -> ActionRecord:
        """校验请求并提交动作，立即返回带编号的排队记录.

        Args:
            request: 包含任务编号、动作名称、参数和执行期限的请求。

        Returns:
            与内部存储隔离的排队记录。

        Raises:
            RuntimeError: 运行时未启动、正在关闭或无法确认设备停止。
            TypeError: 动作参数不是字典。
            ValueError: 请求字段非法或动作未注册。
        """
        # ========== Step1: 参数与运行状态校验 ==========
        if not self._started or self._closing or self._closed:
            raise RuntimeError("Start the runtime before submitting actions")
        if self._blocked_reason is not None:
            raise RuntimeError(f"Runtime blocked: {self._blocked_reason}")
        request = self._validate_request(request)
        skill = self.registry.get(request.skill_name)
        skill.validate(request.args)
        for capability in skill.required_capabilities:
            self.robot.require_capability(capability)
        # ========== Step2: 创建隔离记录并通知消费者 ==========
        record = self.store.create_action(request)
        self._done[record.action_id] = asyncio.Event()
        self._publish(record)
        self._queue.put_nowait(record.action_id)
        logger.info(
            "[ActionManager.submit_action] 动作已排队 task_id=%s action_id=%s skill=%s",
            request.task_id,
            record.action_id,
            request.skill_name,
        )
        return record

    def get_action(self, action_id: int) -> ActionRecord:
        """返回动作记录副本，编号不存在时抛出异常."""
        return self.store.get_action(action_id)

    def list_actions(self, task_id: int | None = None) -> list[ActionRecord]:
        """返回动作副本列表，可按任务编号筛选."""
        return self.store.list_actions(task_id)

    async def wait_for_action(self, action_id: int) -> ActionRecord:
        """等待动作终态，取消等待者不会取消设备动作."""
        record = self.get_action(action_id)
        if not record.status.is_terminal:
            # 只等待通知；取消这个等待者不会取消后台执行的动作。
            _ = await self._done[action_id].wait()
        return self.get_action(action_id)

    async def next_event(self) -> ActionEvent:
        """单消费者事件流，供协调器或控制台接收，无需轮询动作状态."""
        return await self._events.get()

    async def cancel_action(self, action_id: int) -> ActionRecord:
        """请求取消指定动作，执行中动作完成清理后才进入终态.

        Args:
            action_id: 要取消的动作编号。

        Returns:
            当前记录副本；执行中的动作可能仍处于取消中。

        Raises:
            KeyError: 动作编号不存在。
        """
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
        """发布状态事件，进入终态时唤醒动作等待者."""
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
        """校验状态迁移并保存记录，然后发布变化事件."""
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
        logger.info(
            "[ActionManager._transition] 动作状态更新 action_id=%s status=%s",
            action_id,
            status.value,
        )
        return record

    async def _run(self) -> None:
        """按队列顺序处理动作，停止确认失败后拒绝继续执行."""
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
        """依次检查前置条件、执行动作并验证完成证据."""
        await skill.check_preconditions(self.robot)
        result = await skill.execute(self.robot, deepcopy(record.raw_request.args))
        if self.get_action(record.action_id).status == ActionStatus.CANCELLING:
            raise asyncio.CancelledError
        _ = self._transition(record.action_id, ActionStatus.VERIFYING)
        if not await skill.verify(self.robot, result):
            raise RuntimeError("Skill completion verification failed")
        return result

    async def _cleanup(self, skill: RobotSkill) -> None:
        """执行动作清理并再次确认设备已停止."""
        await skill.cleanup(self.robot)
        state = await self.robot.get_state()
        if not state.is_connected or state.is_moving:
            raise RuntimeError("Robot stop could not be confirmed")

    async def _execute(self, record: ActionRecord) -> None:
        """执行单个动作，停止确认完成后才发布终态.

        Args:
            record: 已通过提交校验、等待执行的动作记录。

        执行异常转换为失败或超时记录；清理失败会阻止后续动作。
        """
        # ========== Step1: 建立执行任务与期限 ==========
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
        # ========== Step2: 执行动作并区分超时与取消 ==========
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

        # ========== Step3: 清理并确认设备停止 ==========
        try:
            # 取消执行不等于物理停止，确认停止后才允许下一动作占用设备。
            await asyncio.wait_for(self._cleanup(skill), timeout=self.cleanup_timeout_s)
        except Exception as exc:
            detail = f"Stop/cleanup failed: {type(exc).__name__}: {exc}"
            self._blocked_reason = detail
            logger.error(
                "[ActionManager._execute] 停止确认失败，阻止后续动作 action_id=%s",
                record.action_id,
            )
            failure = f"{failure}; {detail}" if failure else detail
            outcome = ActionStatus.FAILED

        # ========== Step4: 发布终态并释放执行器 ==========
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
        """停止接收动作，取消未完成动作，等待清理，再断开设备."""
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(
                self._close(), name="robot-runtime-close"
            )
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        """等待启动结束，取消剩余动作并释放设备连接."""
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
                    logger.info("[ActionManager._close] 设备已断开，运行时已关闭")
            finally:
                self._started = False
                self._closed = True
