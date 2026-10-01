"""进程内存储；读写都复制数据，避免调用方意外改动正在执行的记录。"""

from collections.abc import Iterator
from copy import deepcopy
from itertools import count

from domain.models import ActionRecord, ActionRequest, TaskState


class InMemoryStore:
    def __init__(self) -> None:
        self._actions: dict[int, ActionRecord] = {}
        self._tasks: dict[int, TaskState] = {}
        self._action_ids: Iterator[int] = count(1)
        self._task_ids: Iterator[int] = count(1)

    def create_action(self, request: ActionRequest) -> ActionRecord:
        record = ActionRecord(
            action_id=next(self._action_ids), raw_request=deepcopy(request)
        )
        self._actions[record.action_id] = deepcopy(record)
        return record

    def get_action(self, action_id: int) -> ActionRecord:
        if action_id not in self._actions:
            raise KeyError(f"Unknown action: {action_id}")
        return deepcopy(self._actions[action_id])

    def save_action(self, record: ActionRecord) -> None:
        if record.action_id not in self._actions:
            raise KeyError(f"Unknown action: {record.action_id}")
        self._actions[record.action_id] = deepcopy(record)

    def list_actions(self, task_id: int | None = None) -> list[ActionRecord]:
        return [
            deepcopy(record)
            for record in self._actions.values()
            if task_id is None or record.raw_request.task_id == task_id
        ]

    def create_task(self, user_target: str) -> TaskState:
        task = TaskState(task_id=next(self._task_ids), user_target=user_target)
        self._tasks[task.task_id] = deepcopy(task)
        return task

    def get_task(self, task_id: int) -> TaskState:
        if task_id not in self._tasks:
            raise KeyError(f"Unknown task: {task_id}")
        return deepcopy(self._tasks[task_id])

    def save_task(self, task: TaskState) -> None:
        if task.task_id not in self._tasks:
            raise KeyError(f"Unknown task: {task.task_id}")
        self._tasks[task.task_id] = deepcopy(task)
