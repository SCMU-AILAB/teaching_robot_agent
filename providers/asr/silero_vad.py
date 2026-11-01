# providers/asr/silero_vad.py
"""Silero 语音检测，经 sherpa-onnx 在 CPU 上运行；按 512 采样窗口切句."""

import array
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

_WINDOW = 512


class _Segment(Protocol):
    """一段已说完的语音."""

    @property
    def samples(self) -> Sequence[float]:
        """归一化到 [-1, 1] 的采样."""
        ...


class _Detector(Protocol):
    """sherpa-onnx VoiceActivityDetector 中实际使用的部分."""

    @property
    def front(self) -> _Segment:
        """最早一段已完成的语音."""
        ...

    def accept_waveform(self, samples: Sequence[float]) -> None:
        """喂入一个窗口的采样."""
        ...

    def empty(self) -> bool:
        """没有已完成的语音段."""
        ...

    def pop(self) -> None:
        """丢弃最早一段."""
        ...

    def is_speech_detected(self) -> bool:
        """当前窗口是否处在语音中."""
        ...

    def reset(self) -> None:
        """清空状态."""
        ...


class _SileroConfig(Protocol):
    """Silero 参数."""

    model: str
    min_silence_duration: float
    min_speech_duration: float
    max_speech_duration: float
    threshold: float
    window_size: int


class _VadConfig(Protocol):
    """VAD 总配置."""

    silero_vad: _SileroConfig
    sample_rate: int
    num_threads: int


class SileroDetector:
    """实现 speech.listener.VoiceDetector；构造时加载模型（约 2 MB）."""

    def __init__(
        self,
        _model_path: str,
        _sample_rate: int = 16000,
        _min_silence_s: float = 0.6,
        _max_speech_s: float = 15.0,
    ) -> None:
        """加载模型.

        Raises:
            FileNotFoundError: 模型文件不存在。
        """
        if not Path(_model_path).is_file():
            raise FileNotFoundError(f"Silero VAD model not found: {_model_path}")
        module = import_module("sherpa_onnx")
        namespace = vars(module)
        config = cast(_VadConfig, cast(object, namespace["VadModelConfig"]()))
        config.silero_vad.model = _model_path
        config.silero_vad.min_silence_duration = _min_silence_s
        config.silero_vad.min_speech_duration = 0.25
        config.silero_vad.max_speech_duration = _max_speech_s
        config.silero_vad.threshold = 0.5
        config.silero_vad.window_size = _WINDOW
        config.sample_rate = _sample_rate
        config.num_threads = 1
        factory = cast(type, namespace["VoiceActivityDetector"])
        self._vad: _Detector = cast(
            _Detector, cast(object, factory(config, buffer_size_in_seconds=30))
        )
        self._pending: array.array[int] = array.array("h")

    @property
    def speaking(self) -> bool:
        """当前窗口处在语音中."""
        return self._vad.is_speech_detected()

    def accept(self, pcm: bytes) -> list[bytes]:
        """缓存到整窗后喂入，返回已说完的句段（十六位 PCM）."""
        self._pending.frombytes(pcm[: len(pcm) - len(pcm) % 2])
        while len(self._pending) >= _WINDOW:
            window = self._pending[:_WINDOW]
            del self._pending[:_WINDOW]
            self._vad.accept_waveform([value / 32768.0 for value in window])
        segments: list[bytes] = []
        while not self._vad.empty():
            samples = self._vad.front.samples
            pcm_out = array.array(
                "h", (max(-32768, min(32767, round(v * 32768.0))) for v in samples)
            )
            segments.append(pcm_out.tobytes())
            self._vad.pop()
        return segments

    def reset(self) -> None:
        """丢弃未完成的句段."""
        self._vad.reset()
        self._pending = array.array("h")
