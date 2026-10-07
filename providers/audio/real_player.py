# providers/audio/real_player.py
"""真实 PortAudio 播放器，依据工作进程的驱动完成确认发布终态."""

import asyncio
import sys
import time
from dataclasses import dataclass

from domain.services import AudioReference, PlaybackState, PlaybackStatus
from domain.validation import nonempty_string
from providers.audio.wav import read_wav
from speech._lifecycle import finish_cleanup
from speech._process import managed_process
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


@dataclass
class _Playback:
    """每次播放的唯一执行与停止作业."""

    task: asyncio.Task[PlaybackState]
    ready: asyncio.Future[None]
    state: PlaybackState
    owner: object
    cleanup: asyncio.Task[PlaybackState] | None = None


class PortAudioPlayer:
    """只有驱动正常排空缓冲才报告完成，中断必须取得停止确认."""

    def __init__(
        self,
        _evidence: EvidenceStore,
        _lease: AudioDeviceLease,
        _device: str = "",
        _worker_command: tuple[str, ...] | None = None,
    ) -> None:
        """注入存储及录音器共用的租约，不在构造期间连接设备."""
        self._evidence: EvidenceStore = _evidence
        self._lease: AudioDeviceLease = _lease
        self._device: str = _device
        self._command: tuple[str, ...] = _worker_command or (
            sys.executable,
            "-m",
            "providers.audio.worker",
        )
        self._items: dict[str, _Playback] = {}
        self._closed: bool = False
        self._close_task: asyncio.Task[None] | None = None
        self.play_count: int = 0

    async def play(self, playback_id: str, audio: AudioReference) -> None:
        """先校验音频，再占用设备；等待真实设备启动确认后返回."""
        playback_id = nonempty_string(playback_id, "playback_id")
        content = await read_wav(self._evidence, audio)
        if self._closed:
            raise RuntimeError("Player closed")
        if playback_id in self._items:
            raise ValueError("Playback already exists")
        owner = object()
        self._lease.acquire(owner)
        ready = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self._play(playback_id, content, audio.duration_s))
        self._items[playback_id] = _Playback(
            task,
            ready,
            PlaybackState(playback_id, PlaybackStatus.PLAYING, time.time()),
            owner,
        )
        task.add_done_callback(self._observe)
        try:
            await asyncio.shield(ready)
        except BaseException:
            _ = await self.stop(playback_id)
            raise

    @staticmethod
    def _observe(task: asyncio.Task[PlaybackState]) -> None:
        """避免未等待的停止错误丢失，结果仍通过等待方法传播."""
        if not task.cancelled():
            _ = task.exception()

    def _terminal(
        self, playback_id: str, status: PlaybackStatus, error: str | None = None
    ) -> PlaybackState:
        """沿用已有领域模型记录实际终态."""
        item = self._items[playback_id]
        item.state = PlaybackState(
            playback_id, status, item.state.started_at, time.time(), error
        )
        return item.state

    async def _confirm_stop(
        self, process: asyncio.subprocess.Process
    ) -> PlaybackStatus:
        """等待设备执行 abort 和 close；强制杀进程不冒充停止确认."""
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        async with asyncio.timeout(3):
            output, _ = await process.communicate()
        if process.returncode == 0 and output.strip() == b"COMPLETED":
            return PlaybackStatus.COMPLETED
        if process.returncode != 2 or output.strip() != b"STOPPED":
            raise RuntimeError("Audio device stop not confirmed")
        return PlaybackStatus.STOPPED

    async def _play(
        self, playback_id: str, content: bytes, duration: float
    ) -> PlaybackState:
        """工作进程的实际驱动反馈决定完成，期限仅用于检测挂起."""
        item = self._items[playback_id]
        started = False
        try:
            # ========== Step1: 送入已验证音频并等待设备就绪 ==========
            async with managed_process(
                self._command + ("play", "--device", self._device)
            ) as process:
                assert process.stdin is not None and process.stdout is not None
                try:
                    async with asyncio.timeout(10):
                        process.stdin.write(content)
                        await process.stdin.drain()
                        process.stdin.close()
                        if await process.stdout.readline() != b"READY\n":
                            raise RuntimeError("Speaker unavailable or failed to start")
                    started = True
                    self.play_count += 1
                    item.ready.set_result(None)
                    # ========== Step2: 等驱动排空并关闭，不按预计时长伪造完成 ==========
                    async with asyncio.timeout(duration + 10):
                        output, _ = await process.communicate()
                    if process.returncode != 0 or output.strip() != b"COMPLETED":
                        raise RuntimeError("Audio playback failed")
                except asyncio.CancelledError:
                    status = PlaybackStatus.STOPPED
                    if started:
                        status = await self._confirm_stop(process)
                    return self._terminal(playback_id, status)
            return self._terminal(playback_id, PlaybackStatus.COMPLETED)
        except Exception as error:
            self._closed = True
            return self._terminal(playback_id, PlaybackStatus.FAILED, str(error))
        finally:
            self._lease.release(item.owner)
            if not item.ready.done():
                item.ready.set_exception(RuntimeError("Speaker failed to start"))
                _ = item.ready.exception()

    async def wait_finished(self, playback_id: str) -> PlaybackState:
        """等待真实终态，外部取消仍需要停止设备."""
        try:
            return await asyncio.shield(self._items[playback_id].task)
        except asyncio.CancelledError:
            _ = await self.stop(playback_id)
            raise

    async def get_state(self, playback_id: str) -> PlaybackState:
        """返回该次播放的领域状态."""
        return self._items[playback_id].state

    async def stop(self, playback_id: str) -> PlaybackState:
        """中途停止必须确认设备退出，停止失败保留失败语义."""
        item = self._items[playback_id]
        if item.cleanup is None:
            if not item.task.done():
                _ = item.task.cancel()
            item.cleanup = asyncio.create_task(self._stop(playback_id))
        return await finish_cleanup(item.cleanup)

    async def _stop(self, playback_id: str) -> PlaybackState:
        """即便执行任务未启动，也回收其租约并唤醒启动等待者."""
        item = self._items[playback_id]
        _ = await asyncio.gather(item.task, return_exceptions=True)
        self._lease.release(item.owner)
        if not item.ready.done():
            _ = item.ready.cancel()
        if item.task.cancelled():
            _ = self._terminal(playback_id, PlaybackStatus.STOPPED)
        if item.state.status == PlaybackStatus.FAILED:
            raise RuntimeError(item.state.error or "Playback failed")
        return item.state

    async def close(self) -> None:
        """停止全部播放并释放设备，调用方取消不打断回收."""
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await finish_cleanup(self._close_task)

    async def _close(self) -> None:
        """收集全部停止结果后传播失败，不跳过剩余清理."""
        results = await asyncio.gather(
            *(self.stop(key) for key in self._items), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
