# storage/memory.py
"""进程内存储；读写都复制数据，避免调用方意外改动正在执行的记录.

记录达到上限时清理最早的已结束任务及其动作；未结束的任务和动作永不清理，
全部记录都未结束时才拒绝新建。
"""

from collections.abc import Iterator
from copy import deepcopy
from itertools import count

from domain.models import ActionRecord, ActionRequest, TaskState, TaskStatus
from domain.validation import positive_int

_TASK_TERMINAL = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


class InMemoryStore:
    """通过复制隔离调用方与内部任务、动作记录."""

    def __init__(self, _max_records: int = 4096) -> None:
        """初始化依赖与实例状态，不启动后台任务.

        Args:
            _max_records: 任务与动作各自保留的记录上限.
        """
        self._actions: dict[int, ActionRecord] = {}
        self._tasks: dict[int, TaskState] = {}
        self._action_ids: Iterator[int] = count(1)
        self._task_ids: Iterator[int] = count(1)
        self._max_records: int = positive_int(_max_records, "max_records")

    def create_action(self, request: ActionRequest) -> ActionRecord:
        """为请求分配动作编号并保存独立记录."""
        if len(self._actions) >= self._max_records and not self._evict_action():
            raise RuntimeError(
                "Action record capacity reached; archive before continuing"
            )
        record = ActionRecord(
            action_id=next(self._action_ids), raw_request=deepcopy(request)
        )
        self._actions[record.action_id] = deepcopy(record)
        return record

    def get_action(self, action_id: int) -> ActionRecord:
        """返回动作记录副本，编号不存在时抛出异常."""
        if action_id not in self._actions:
            raise KeyError(f"Unknown action: {action_id}")
        return deepcopy(self._actions[action_id])

    def save_action(self, record: ActionRecord) -> None:
        """以副本更新已有动作记录."""
        if record.action_id not in self._actions:
            raise KeyError(f"Unknown action: {record.action_id}")
        self._actions[record.action_id] = deepcopy(record)

    def list_actions(self, task_id: int | None = None) -> list[ActionRecord]:
        """返回动作副本列表，可按任务编号筛选."""
        return [
            deepcopy(record)
            for record in self._actions.values()
            if task_id is None or record.raw_request.task_id == task_id
        ]

    def create_task(self, user_target: str) -> TaskState:
        """创建任务并保存用户目标."""
        if len(self._tasks) >= self._max_records and not self._evict_task():
            raise RuntimeError(
                "Task record capacity reached; archive before continuing"
            )
        task = TaskState(task_id=next(self._task_ids), user_target=user_target)
        self._tasks[task.task_id] = deepcopy(task)
        return task

    def get_task(self, task_id: int) -> TaskState:
        """返回任务记录副本，编号不存在时抛出异常."""
        if task_id not in self._tasks:
            raise KeyError(f"Unknown task: {task_id}")
        return deepcopy(self._tasks[task_id])

    def save_task(self, task: TaskState) -> None:
        """以副本更新已有任务记录."""
        if task.task_id not in self._tasks:
            raise KeyError(f"Unknown task: {task.task_id}")
        self._tasks[task.task_id] = deepcopy(task)

    def _task_finished(self, task_id: int) -> bool:
        """任务已结束且其动作全部进入终态；任务记录已清理的视为已结束."""
        task = self._tasks.get(task_id)
        if task is None:
            return True
        return task.status in _TASK_TERMINAL and all(
            self._actions[item].status.is_terminal
            for item in task.action_ids
            if item in self._actions
        )

    def _evict_task(self) -> bool:
        """清理最早一个已结束任务及其动作，没有可清理的返回 False."""
        for task_id in self._tasks:
            if self._task_finished(task_id):
                task = self._tasks.pop(task_id)
                for action_id in task.action_ids:
                    _ = self._actions.pop(action_id, None)
                return True
        return False

    def _evict_action(self) -> bool:
        """清理最早一个所属任务已结束的终态动作，没有可清理的返回 False."""
        for action_id, record in self._actions.items():
            if record.status.is_terminal and self._task_finished(
                record.raw_request.task_id
            ):
                del self._actions[action_id]
                return True
        return False
