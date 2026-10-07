# providers/audio/recorder.py
"""真实 PortAudio 录音器，按会话托管并回收设备子进程."""

import asyncio
import signal
import sys
import time
from dataclasses import dataclass

from domain.services import AudioReference
from domain.validation import finite_float, nonempty_string, positive_int
from providers.audio.wav import save_wav
from speech._lifecycle import finish_cleanup
from speech._process import managed_process
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


@dataclass
class _Recording:
    """同一编号的所有结束请求共享结果及设备所有权."""

    task: asyncio.Task[AudioReference]
    ready: asyncio.Future[None]
    owner: object
    process: asyncio.subprocess.Process | None = None
    cleanup: asyncio.Task[None] | None = None


class PortAudioRecorder:
    """采集真实单声道十六位 PCM；无设备、空帧和失败均明确报错."""

    def __init__(
        self,
        _evidence: EvidenceStore,
        _lease: AudioDeviceLease,
        _device: str = "",
        _sample_rate: int = 16000,
        _worker_command: tuple[str, ...] | None = None,
    ) -> None:
        """注入存储、共享租约及设备配置；构造时不打开设备."""
        self._evidence: EvidenceStore = _evidence
        self._lease: AudioDeviceLease = _lease
        self._device: str = _device
        self._rate: int = positive_int(_sample_rate, "sample_rate")
        if not 8000 <= self._rate <= 48000:
            raise ValueError("Recording sample rate must be 8000..48000")
        self._command: tuple[str, ...] = _worker_command or (
            sys.executable,
            "-m",
            "providers.audio.worker",
        )
        self._sessions: dict[str, _Recording] = {}
        self._closed: bool = False
        self._close_task: asyncio.Task[None] | None = None
        self.finalize_count: int = 0

    async def start(self, recording_id: str, max_duration_s: float) -> None:
        """等待设备启动确认；最长时长由设备工作进程自动执行."""
        recording_id = nonempty_string(recording_id, "recording_id")
        duration = finite_float(max_duration_s, "max_duration_s")
        if not 0 < duration <= 60:
            raise ValueError("Recording duration must be in (0, 60]")
        if self._closed:
            raise RuntimeError("Recorder closed")
        if recording_id in self._sessions:
            raise ValueError("Recording already exists")
        owner = object()
        self._lease.acquire(owner)
        ready = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self._record(recording_id, duration))
        self._sessions[recording_id] = _Recording(task, ready, owner)
        task.add_done_callback(self._observe)
        try:
            await asyncio.shield(ready)
        except BaseException:
            await self.cancel(recording_id)
            raise

    @staticmethod
    def _observe(task: asyncio.Task[AudioReference]) -> None:
        """观察无人等待的自动结束错误，仍允许调用方取得原始异常."""
        if not task.cancelled():
            _ = task.exception()

    async def _record(self, recording_id: str, duration: float) -> AudioReference:
        """设备确认后收集唯一音频，先关闭设备再保存证据."""
        session = self._sessions[recording_id]
        try:
            # ========== Step1: 启动设备工作进程并等待明确就绪 ==========
            command = self._command + (
                "record",
                "--device",
                self._device,
                "--rate",
                str(self._rate),
                "--duration",
                str(duration),
            )
            async with asyncio.timeout(duration + 15):
                async with managed_process(command) as process:
                    session.process = process
                    assert process.stdout is not None
                    async with asyncio.timeout(10):
                        if await process.stdout.readline() != b"READY\n":
                            raise RuntimeError(
                                "Microphone unavailable or failed to start"
                            )
                    started_at = time.time()
                    session.ready.set_result(None)
                    content, _ = await process.communicate()
                    if process.returncode != 0:
                        raise RuntimeError("Recording failed")
            # ========== Step2: 工作进程退出后登记一次可读取音频 ==========
            audio = await save_wav(self._evidence, content, started_at)
            self.finalize_count += 1
            return audio
        finally:
            self._lease.release(session.owner)
            if not session.ready.done():
                session.ready.set_exception(RuntimeError("Microphone failed to start"))
                _ = session.ready.exception()

    async def finish(self, recording_id: str) -> AudioReference:
        """信号结束采集；自动结束与重复结束返回同一份音频."""
        session = self._sessions[recording_id]
        try:
            await asyncio.shield(session.ready)
            process = session.process
            if (
                not session.task.done()
                and process is not None
                and process.returncode is None
            ):
                try:
                    process.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
            return await self.wait_finished(recording_id)
        except asyncio.CancelledError:
            await self.cancel(recording_id)
            raise

    async def wait_finished(self, recording_id: str) -> AudioReference:
        """外部等待被取消时，先确认录音子进程退出再传播取消."""
        try:
            return await asyncio.shield(self._sessions[recording_id].task)
        except asyncio.CancelledError:
            await self.cancel(recording_id)
            raise

    async def cancel(self, recording_id: str) -> None:
        """丢弃当前录音并等待设备进程退出，不保存取消后的帧."""
        session = self._sessions[recording_id]
        if session.cleanup is None:
            _ = session.task.cancel()
            session.cleanup = asyncio.create_task(self._cancel(session))
        await finish_cleanup(session.cleanup)

    async def _cancel(self, session: _Recording) -> None:
        """任务尚未开始时也归还租约并唤醒启动等待者."""
        _ = await asyncio.gather(session.task, return_exceptions=True)
        self._lease.release(session.owner)
        if not session.ready.done():
            _ = session.ready.cancel()

    async def close(self) -> None:
        """拒绝新录音并关闭所有会话，重复取消不打断清理."""
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await finish_cleanup(self._close_task)

    async def _close(self) -> None:
        """取消并等待全部设备进程回收."""
        for recording_id in self._sessions:
            await self.cancel(recording_id)
