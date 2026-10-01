# speech/interfaces.py
"""语音负责人实现的接口，任务关联与识别去重由核心负责."""

from typing import Protocol

from domain.services import AudioReference, PlaybackState, TranscriptResult


class AudioRecorder(Protocol):
    """录音结束与取消必须释放麦克风."""

    async def start(self, recording_id: str, max_duration_s: float) -> None:
        """开始采集，达到最长时长自动结束."""
        ...

    async def finish(self, recording_id: str) -> AudioReference:
        """结束录音，多次调用返回同一份音频引用."""
        ...

    async def wait_finished(self, recording_id: str) -> AudioReference:
        """等待主动结束或达到最长时长，取消时抛出 CancelledError."""
        ...

    async def cancel(self, recording_id: str) -> None:
        """停止并丢弃输入，唤醒等待者."""
        ...

    async def close(self) -> None:
        """关闭所有录音并释放设备."""
        ...


class ASRProvider(Protocol):
    """最终转写由核心统一提交给教学流程."""

    async def transcribe(
        self, audio: AudioReference, timeout_s: float
    ) -> TranscriptResult:
        """识别已登记音频，无语音返回空文本."""
        ...


class TTSProvider(Protocol):
    """合成服务只产生音频，不自行开始播放."""

    async def synthesize(
        self, text: str, voice_id: str | None, timeout_s: float
    ) -> AudioReference:
        """生成登记音频，取消后不得触发播放."""
        ...


class AudioPlayer(Protocol):
    """通过实际播放反馈区分完成、停止与失败."""

    async def play(self, playback_id: str, audio: AudioReference) -> None:
        """开始播放，返回不代表已经播完."""
        ...

    async def wait_finished(self, playback_id: str) -> PlaybackState:
        """等待终态，必须返回 completed、stopped 或 failed."""
        ...

    async def stop(self, playback_id: str) -> PlaybackState:
        """停止指定播放并确认终态，重复调用安全."""
        ...

    async def get_state(self, playback_id: str) -> PlaybackState:
        """读取真实播放状态."""
        ...

    async def close(self) -> None:
        """停止播放并释放音频设备."""
        ...
