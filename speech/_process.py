# speech/_process.py
"""语音子进程的启动与取消清理，不使用 shell 或后台阻塞线程."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from speech._lifecycle import finish_cleanup


async def _reap(process: asyncio.subprocess.Process) -> None:
    """先终止再回收；不响应终止的子进程强制退出后也必须回收."""
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        async with asyncio.timeout(3):
            _ = await process.communicate()
    except TimeoutError:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        _ = await process.communicate()


async def _reap_start(task: asyncio.Task[asyncio.subprocess.Process]) -> None:
    """取消恰逢创建进程时仍取得句柄并关闭，避免孤儿进程."""
    process = await task
    await _reap(process)


@asynccontextmanager
async def managed_process(
    command: tuple[str, ...],
) -> AsyncGenerator[asyncio.subprocess.Process]:
    """托管单个工作进程，任何退出路径均等待回收.

    Args:
        command: 可信装配方提供的参数列表，不进行 shell 展开。

    Yields:
        带标准输入输出管道的子进程。
    """
    starting = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    )
    try:
        process = await asyncio.shield(starting)
    except asyncio.CancelledError:
        await finish_cleanup(asyncio.create_task(_reap_start(starting)))
        raise
    try:
        yield process
    finally:
        await finish_cleanup(asyncio.create_task(_reap(process)))


async def run_process(
    command: tuple[str, ...], content: bytes, timeout_s: float
) -> bytes:
    """在期限内读取工作进程结果，取消或超时后终止并回收进程."""
    async with asyncio.timeout(timeout_s):
        async with managed_process(command) as process:
            output, _ = await process.communicate(content)
            if process.returncode != 0:
                raise RuntimeError("Speech worker failed; check engine configuration")
            return output
