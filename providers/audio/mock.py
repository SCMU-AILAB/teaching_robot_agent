# providers/audio/mock.py
"""使用单个完成任务生成确定性 WAV，不连接麦克风."""

import asyncio
import io
import time
import wave
from dataclasses import dataclass
from uuid import uuid4

from domain.services import AudioReference
from domain.validation import finite_float, nonempty_string
from speech._lifecycle import finish_cleanup
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


@dataclass
class _Recording:
    """仅用于模拟器内部的录音生命周期."""

    stop: asyncio.Event
    task: asyncio.Task[AudioReference]
    cleanup: asyncio.Task[None] | None = None


class MockAudioRecorder:
    """主动结束和自动结束共享一个完成任务，取消唤醒全部等待者."""

    def __init__(
        self, _evidence: EvidenceStore, _device: AudioDeviceLease | None = None
    ) -> None:
        """注入证据存储，不创建后台任务."""
        self._evidence: EvidenceStore = _evidence
        self._recordings: dict[str, _Recording] = {}
        self._closed: bool = False
        self.finalize_count: int = 0
        self._device: AudioDeviceLease = _device or AudioDeviceLease()
        self._close_task: asyncio.Task[None] | None = None

    async def start(self, recording_id: str, max_duration_s: float) -> None:
        """开始模拟录音，拒绝重用编号和并发占用."""
        recording_id = nonempty_string(recording_id, "recording_id")
        duration = finite_float(max_duration_s, "max_duration_s")
        if duration <= 0:
            raise ValueError("max_duration_s must be positive")
        if self._closed:
            raise RuntimeError("Recorder closed")
        if recording_id in self._recordings:
            raise ValueError("Recording already exists")
        if any(not item.task.done() for item in self._recordings.values()):
            raise RuntimeError("Recorder busy")
        stop = asyncio.Event()
        self._device.acquire(stop)
        task = asyncio.create_task(self._record_owned(stop, duration))
        task.add_done_callback(self._observe_failure)
        self._recordings[recording_id] = _Recording(stop, task)

    @staticmethod
    def _observe_failure(task: asyncio.Task[AudioReference]) -> None:
        """观察自动结束异常，仍保留结果供调用方等待."""
        if not task.cancelled():
            _ = task.exception()

    async def _record_owned(
        self, stop: asyncio.Event, duration: float
    ) -> AudioReference:
        """生成音频后释放设备，失败和取消也释放."""
        try:
            return await self._record(stop, duration)
        finally:
            self._device.release(stop)

    async def _record(self, stop: asyncio.Event, duration: float) -> AudioReference:
        """等待结束信号并只保存一次固定音频."""
        # ========== Step1: 等待主动结束或最长时长 ==========
        started_at = time.time()
        try:
            async with asyncio.timeout(duration):
                _ = await stop.wait()
        except TimeoutError:
            pass
        # ========== Step2: 生成固定的 16 kHz 单声道 PCM WAV ==========
        stream = io.BytesIO()
        with wave.open(stream, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 160)
        reference = await self._evidence.save(
            stream.getvalue(), "audio/wav", started_at
        )
        self.finalize_count += 1
        # 固定测试片段时长为 10 ms；时间戳记录模拟会话的实际起止时间。
        return AudioReference(
            uuid4().hex, reference.evidence_id, started_at, time.time(), 16000, 1, 0.01
        )

    async def finish(self, recording_id: str) -> AudioReference:
        """主动结束并返回同一个结果，取消等待者时清理录音."""
        self._recordings[recording_id].stop.set()
        return await self.wait_finished(recording_id)

    async def wait_finished(self, recording_id: str) -> AudioReference:
        """等待共享完成结果，外部取消也等待录音任务退出."""
        task = self._recordings[recording_id].task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await self.cancel(recording_id)
            raise

    async def cancel(self, recording_id: str) -> None:
        """取消录音任务；等待者得到 CancelledError，取消方正常返回."""
        item = self._recordings[recording_id]
        if item.cleanup is None:
            _ = item.task.cancel()
            item.cleanup = asyncio.create_task(self._cancel_recording(item))
        await finish_cleanup(item.cleanup)

    async def _cancel_recording(self, item: _Recording) -> None:
        """任务尚未开始也必须释放占用."""
        _ = await asyncio.gather(item.task, return_exceptions=True)
        self._device.release(item.stop)

    async def close(self) -> None:
        """禁止新录音并等待所有后台任务退出，多次关闭安全."""
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await finish_cleanup(self._close_task)

    async def _close(self) -> None:
        """清理全部录音，不受关闭等待者取消影响."""
        for recording_id in self._recordings:
            await self.cancel(recording_id)
