# speech/_lifecycle.py
"""语音资源清理的取消隔离工具."""

import asyncio


async def finish_cleanup[T](task: asyncio.Task[T]) -> T:
    """清理完成后再传播调用方取消，重复取消不打断资源释放.

    Args:
        task: 已创建且由资源拥有者保存的唯一清理任务。

    Returns:
        清理任务的结果。

    Raises:
        asyncio.CancelledError: 调用方或清理任务被取消。
        Exception: 清理失败，原始异常继续传播。
    """
    interrupted = False
    while not task.done():
        try:
            _ = await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            interrupted = True
    result = task.result()
    if interrupted:
        raise asyncio.CancelledError
    return result
