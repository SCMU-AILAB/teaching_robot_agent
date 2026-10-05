# runtime/task_coordinator.py
"""任务聚合：单个动作完成后，任务仍可继续提交下一步动作."""

import asyncio
import logging
import time

from domain.education import TeachingSession, TeachingStage
from domain.models import (
    ActionRecord,
    ActionRequest,
    ActionStatus,
    TaskState,
    TaskStatus,
)
from domain.validation import nonempty_string
from runtime.action_manager import ActionManager
from storage.memory import InMemoryStore

logger = logging.getLogger(__name__)

_TASK_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class TaskCoordinator:
    """关联任务与动作，并管理显式结束和任务取消."""

    def __init__(
        self, _runtime: ActionManager, _store: InMemoryStore | None = None
    ) -> None:
        """初始化依赖与实例状态，不启动后台任务."""
        self.runtime: ActionManager = _runtime
        self.store: InMemoryStore = _runtime.store if _store is None else _store
        if self.store is not _runtime.store:
            raise ValueError("Coordinator and runtime must share the same store")
        self._cancellations: dict[int, asyncio.Task[None]] = {}

    def create_task(self, user_target: str) -> TaskState:
        """创建任务并保存用户目标."""
        return self.store.create_task(
            nonempty_string(user_target, "user_target").strip()
        )

    def get_task(self, task_id: int) -> TaskState:
        """返回任务记录副本，编号不存在时抛出异常."""
        return self.store.get_task(task_id)

    async def submit_action(
        self,
        task_id: int,
        skill_name: str,
        args: dict[str, object],
        timeout_s: float = 10.0,
    ) -> ActionRecord:
        """向可执行的任务提交动作，并保存动作与任务的关联.

        Args:
            task_id: 已创建的任务编号。
            skill_name: 已注册的动作名称。
            args: 由动作实现校验的参数字典。
            timeout_s: 执行期限，单位为秒，不包含排队时间。

        Returns:
            新创建的排队动作记录。

        Raises:
            KeyError: 任务不存在。
            ValueError: 任务不再接受动作或请求参数非法。
            RuntimeError: 动作运行时当前不可用。
            TypeError: 动作参数不是字典。
        """
        task = self.get_task(task_id)
        if task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            raise ValueError(f"Task {task_id} no longer accepts actions")
        record = await self.runtime.submit_action(
            ActionRequest(
                task_id=task_id, skill_name=skill_name, args=args, timeout_s=timeout_s
            )
        )
        task.action_ids.append(record.action_id)
        task.status = TaskStatus.RUNNING
        self.store.save_task(task)
        logger.info(
            "[TaskCoordinator.submit_action] 任务已关联动作 task_id=%s action_id=%s",
            task_id,
            record.action_id,
        )
        return record

    def complete_task(
        self,
        task_id: int,
        *,
        teaching: TeachingSession | None = None,
        interaction_completed: bool = False,
    ) -> TaskState:
        """由调用方明确结束任务；动作成功不会提前关闭多步骤任务."""
        task = self.get_task(task_id)
        if task.status in _TASK_TERMINAL:
            return task
        if task.status == TaskStatus.CANCELLING:
            raise ValueError("Wait for task cancellation to finish")
        records = [self.runtime.get_action(item) for item in task.action_ids]
        if teaching is not None and (
            teaching.task_id != task_id or teaching.stage != TeachingStage.COMPLETED
        ):
            raise ValueError("Teaching completion must belong to this task")
        if any(not item.status.is_terminal for item in records):
            raise ValueError("All actions must be finished")
        if not records and teaching is None and not interaction_completed:
            raise ValueError("Empty tasks require teaching completion evidence")
        task.status = (
            TaskStatus.COMPLETED
            if all(item.status == ActionStatus.SUCCEEDED for item in records)
            else TaskStatus.FAILED
        )
        task.ended_at = time.time()
        self.store.save_task(task)
        return task

    async def cancel_task(self, task_id: int) -> TaskState:
        """取消整个任务并等待关联动作停止.

        Args:
            task_id: 要取消的任务编号。

        Returns:
            最终任务记录；停止失败或已有失败动作时为失败状态。

        Raises:
            KeyError: 任务编号不存在。

        调用方取消等待时，后台仍继续清理并保存任务终态。
        """
        task = self.get_task(task_id)
        if task.status in _TASK_TERMINAL:
            return task
        task.status = TaskStatus.CANCELLING
        self.store.save_task(task)
        if task_id not in self._cancellations:
            self._cancellations[task_id] = asyncio.create_task(
                self._cancel_task(task_id), name=f"robot-task-cancel-{task_id}"
            )
        # API 请求断开或等待者取消后，任务仍要完成停止与终态更新。
        await asyncio.shield(self._cancellations[task_id])
        return self.get_task(task_id)

    async def _cancel_task(self, task_id: int) -> None:
        """取消全部关联动作，等待停止后保存任务终态."""
        task = self.get_task(task_id)
        for action_id in task.action_ids:
            _ = await self.runtime.cancel_action(action_id)
        records = await asyncio.gather(
            *(self.runtime.wait_for_action(action_id) for action_id in task.action_ids)
        )
        task.status = (
            TaskStatus.FAILED
            if any(
                item.status in {ActionStatus.FAILED, ActionStatus.TIMED_OUT}
                for item in records
            )
            else TaskStatus.CANCELLED
        )
        task.ended_at = time.time()
        self.store.save_task(task)
        logger.info(
            "[TaskCoordinator._cancel_task] 任务取消流程结束 task_id=%s status=%s",
            task_id,
            task.status.value,
        )
