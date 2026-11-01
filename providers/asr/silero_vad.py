# providers/asr/silero_vad.py
"""Silero 语音检测，经 sherpa-onnx 在 CPU 上运行；按 512 采样窗口切句."""

import array
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

_WINDOW = 512
# 句段前后补的时长：检测会把开头和句尾的轻声判成静音（真机上句尾两个字被吞）；
# 不靠降低阈值解决：阈值 0.4 时房间底噪被判成语音，一句话拖到上限才切出（2026-11-01 实测）。
# 判定一句结束要先等到静音，句尾之后的声音此时已在缓存里，补上不增加等待
_PRE_ROLL_S = 0.3
_POST_ROLL_S = 0.6
_HISTORY_S = 30


class _Segment(Protocol):
    """一段已说完的语音."""

    @property
    def start(self) -> int:
        """起点：自上次重置以来的采样序号."""
        ...

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
        _max_speech_s: float = 10.0,
        _threshold: float = 0.5,
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
        config.silero_vad.threshold = _threshold
        config.silero_vad.window_size = _WINDOW
        config.sample_rate = _sample_rate
        config.num_threads = 1
        factory = cast(type, namespace["VoiceActivityDetector"])
        self._vad: _Detector = cast(
            _Detector, cast(object, factory(config, buffer_size_in_seconds=30))
        )
        self._pending: array.array[int] = array.array("h")
        # 不成对的末尾字节留到下一块，避免采样错位
        self._leftover: bytes = b""
        self._rate: int = _sample_rate
        # 最近的原始采样及其中第一个采样的序号（自上次重置起），用于前后补音
        self._history: array.array[int] = array.array("h")
        self._history_start: int = 0
        self._fed: int = 0

    @property
    def speaking(self) -> bool:
        """当前窗口处在语音中."""
        return self._vad.is_speech_detected()

    def accept(self, pcm: bytes) -> list[bytes]:
        """缓存到整窗后喂入，返回已说完的句段（十六位 PCM）."""
        data = self._leftover + pcm
        even = len(data) - len(data) % 2
        self._leftover = data[even:]
        self._pending.frombytes(data[:even])
        while len(self._pending) >= _WINDOW:
            window = self._pending[:_WINDOW]
            del self._pending[:_WINDOW]
            self._vad.accept_waveform([value / 32768.0 for value in window])
            self._history.extend(window)
            self._fed += _WINDOW
        overflow = len(self._history) - _HISTORY_S * self._rate
        if overflow > 0:
            del self._history[:overflow]
            self._history_start += overflow
        segments: list[bytes] = []
        while not self._vad.empty():
            segment = self._vad.front
            first = segment.start - int(_PRE_ROLL_S * self._rate)
            last = segment.start + len(segment.samples) + int(_POST_ROLL_S * self._rate)
            begin = max(first, self._history_start) - self._history_start
            end = min(last, self._fed) - self._history_start
            segments.append(self._history[begin:end].tobytes())
            self._vad.pop()
        return segments

    def reset(self) -> None:
        """丢弃未完成的句段."""
        self._vad.reset()
        self._pending = array.array("h")
        self._leftover = b""
        self._history = array.array("h")
        self._history_start = 0
        self._fed = 0
