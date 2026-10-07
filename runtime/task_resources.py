# runtime/task_resources.py
"""任务附属资源的停止与完成屏障，不绑定具体设备或服务."""

from typing import Protocol


class TaskResource(Protocol):
    """任务终态发布前必须释放的录音或其他附属作业."""

    def is_busy(self, task_id: int) -> bool:
        """判断任务是否仍有在途资源，禁止提前验收."""
        ...

    async def cancel_task(self, task_id: int) -> None:
        """停止此任务的资源并等待确认，失败抛出异常."""
        ...
