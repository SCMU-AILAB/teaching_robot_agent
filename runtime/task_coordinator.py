"""任务聚合：单个动作完成后，任务仍可继续提交下一步动作。"""

import asyncio
import time

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

_TASK_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class TaskCoordinator:
    def __init__(
        self, runtime: ActionManager, store: InMemoryStore | None = None
    ) -> None:
        self.runtime: ActionManager = runtime
        self.store: InMemoryStore = runtime.store if store is None else store
        if self.store is not runtime.store:
            raise ValueError("Coordinator and runtime must share the same store")
        self._cancellations: dict[int, asyncio.Task[None]] = {}

    def create_task(self, user_target: str) -> TaskState:
        return self.store.create_task(
            nonempty_string(user_target, "user_target").strip()
        )

    def get_task(self, task_id: int) -> TaskState:
        return self.store.get_task(task_id)

    async def submit_action(
        self,
        task_id: int,
        skill_name: str,
        args: dict[str, object],
        timeout_s: float = 10.0,
    ) -> ActionRecord:
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
        return record

    def complete_task(self, task_id: int) -> TaskState:
        """由调用方明确结束任务；动作成功不会提前关闭多步骤任务。"""
        task = self.get_task(task_id)
        if task.status in _TASK_TERMINAL:
            return task
        if task.status == TaskStatus.CANCELLING:
            raise ValueError("Wait for task cancellation to finish")
        records = [self.runtime.get_action(item) for item in task.action_ids]
        if not records or any(not item.status.is_terminal for item in records):
            raise ValueError("Task must have actions, and all actions must be finished")
        task.status = (
            TaskStatus.COMPLETED
            if all(item.status == ActionStatus.SUCCEEDED for item in records)
            else TaskStatus.FAILED
        )
        task.ended_at = time.time()
        self.store.save_task(task)
        return task

    async def cancel_task(self, task_id: int) -> TaskState:
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
