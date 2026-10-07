# providers/audio/player.py
"""按音频真实时长模拟播放，不打开扬声器."""

import asyncio
import io
import math
import time
import wave
from dataclasses import dataclass, replace

from domain.services import AudioReference, PlaybackState, PlaybackStatus
from domain.validation import finite_float, nonempty_string
from speech._lifecycle import finish_cleanup
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


@dataclass
class _Playback:
    """内部播放记录，直接复用领域播放状态."""

    state: PlaybackState
    stop: asyncio.Event
    task: asyncio.Task[PlaybackState] | None = None


class MockAudioPlayer:
    """模拟实际播放时间及停止确认，拒绝重复编号与设备占用."""

    def __init__(
        self,
        _evidence: EvidenceStore,
        _device: AudioDeviceLease | None = None,
        _time_scale: float = 1.0,
        _error: Exception | None = None,
    ) -> None:
        """注入证据存储、共享占用对象和播放失败模拟."""
        self._evidence: EvidenceStore = _evidence
        self._device: AudioDeviceLease = _device or AudioDeviceLease()
        self._time_scale: float = finite_float(_time_scale, "time_scale")
        if self._time_scale <= 0:
            raise ValueError("time_scale must be positive")
        self._error: Exception | None = _error
        self._playbacks: dict[str, _Playback] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._closed: bool = False
        self._close_task: asyncio.Task[None] | None = None
        self.started: asyncio.Event = asyncio.Event()
        self.play_count: int = 0

    async def play(self, playback_id: str, audio: AudioReference) -> None:
        """校验 WAV 后开始模拟播放，返回不代表播完.

        Args:
            playback_id: 本次播放的唯一编号。
            audio: 存储中可读取的音频引用。

        Raises:
            ValueError: 编号重复、音频损坏或元数据不匹配。
            KeyError: 证据不存在。
            RuntimeError: 播放器已关闭或设备被占用。
        """
        playback_id = nonempty_string(playback_id, "playback_id")
        async with self._lock:
            if self._closed:
                raise RuntimeError("Player closed")
            if playback_id in self._playbacks:
                raise ValueError("Playback already exists")
            # ========== Step1: 校验媒体及实际音频格式 ==========
            duration = finite_float(audio.duration_s, "duration_s")
            if duration <= 0:
                raise ValueError("Audio duration must be positive")
            if self._evidence.get(audio.evidence_id).media_type != "audio/wav":
                raise ValueError("Expected WAV evidence")
            content = await self._evidence.read(audio.evidence_id)
            try:
                with wave.open(io.BytesIO(content), "rb") as wav:
                    frame_count = wav.getnframes()
                    if (
                        wav.getframerate() != audio.sample_rate_hz
                        or wav.getnchannels() != audio.channels
                        or wav.getsampwidth() != 2
                        or not math.isclose(frame_count / wav.getframerate(), duration)
                        or len(wav.readframes(frame_count))
                        != frame_count * wav.getnchannels() * wav.getsampwidth()
                    ):
                        raise ValueError("Audio metadata or PCM content mismatch")
            except (wave.Error, EOFError) as error:
                raise ValueError("Invalid WAV evidence") from error
            # ========== Step2: 取得设备占用并启动唯一播放任务 ==========
            if self._closed:
                raise RuntimeError("Player closed")
            stop = asyncio.Event()
            self._device.acquire(stop)
            item = _Playback(
                PlaybackState(playback_id, PlaybackStatus.PLAYING, time.time()), stop
            )
            self._playbacks[playback_id] = item
            item.task = asyncio.create_task(self._play(item, duration))
            self.play_count += 1
            self.started.set()

    async def _play(self, item: _Playback, duration: float) -> PlaybackState:
        """结束后记录终态并释放占用，停止不能报告为自然完成."""
        status = PlaybackStatus.COMPLETED
        failure = None
        try:
            if self._error is not None:
                raise self._error
            try:
                async with asyncio.timeout(duration * self._time_scale):
                    _ = await item.stop.wait()
                status = PlaybackStatus.STOPPED
            except TimeoutError:
                pass
        except Exception as error:
            status = PlaybackStatus.FAILED
            failure = str(error)
        finally:
            self._device.release(item.stop)
        item.state = replace(
            item.state, status=status, ended_at=time.time(), error=failure
        )
        return item.state

    async def wait_finished(self, playback_id: str) -> PlaybackState:
        """等待播放终态；取消等待时先停止播放再传播取消."""
        item = self._playbacks[playback_id]
        assert item.task is not None
        try:
            return await asyncio.shield(item.task)
        except asyncio.CancelledError:
            _ = await self.stop(playback_id)
            raise

    async def stop(self, playback_id: str) -> PlaybackState:
        """请求停止并确认终态，重复停止复用原始结果."""
        item = self._playbacks[playback_id]
        item.stop.set()
        assert item.task is not None
        return await finish_cleanup(item.task)

    async def get_state(self, playback_id: str) -> PlaybackState:
        """返回不可变的当前播放状态."""
        return self._playbacks[playback_id].state

    async def close(self) -> None:
        """关闭等待者取消也必须等待全部播放停止."""
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await finish_cleanup(self._close_task)

    async def _close(self) -> None:
        """持锁等待正在开始的播放，然后逐个确认停止."""
        async with self._lock:
            for item in self._playbacks.values():
                item.stop.set()
            for playback_id in self._playbacks:
                _ = await self.stop(playback_id)
