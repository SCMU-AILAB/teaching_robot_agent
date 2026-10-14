# providers/asr/whisper_engine.py
"""faster-whisper 调用边界：子进程与常驻识别共用的类型和转写步骤."""

import io
from collections.abc import Iterable
from importlib import import_module
from typing import Protocol, cast


class WhisperSegment(Protocol):
    """识别引擎的文本段."""

    @property
    def text(self) -> str:
        """该段识别文本."""
        ...


class WhisperInfo(Protocol):
    """识别引擎提供的语言信息."""

    @property
    def language(self) -> str:
        """识别出的语言代码."""
        ...


class WhisperModel(Protocol):
    """仅声明实际使用的 faster-whisper 调用边界."""

    def transcribe(
        self,
        audio: io.BytesIO,
        *,
        language: str,
        beam_size: int,
        vad_filter: bool,
        condition_on_previous_text: bool,
    ) -> tuple[Iterable[WhisperSegment], WhisperInfo]:
        """返回惰性识别段与语言信息."""
        ...


class WhisperModelFactory(Protocol):
    """真实模型构造函数的类型边界."""

    def __call__(
        self,
        model_size_or_path: str,
        *,
        device: str,
        compute_type: str,
        local_files_only: bool,
        cpu_threads: int,
    ) -> WhisperModel:
        """从本地目录加载模型."""
        ...


def load_faster_whisper() -> WhisperModelFactory:
    """调用时才导入可选依赖，未安装语音依赖的应用仍可导入本模块."""
    module = import_module("faster_whisper")
    return cast(WhisperModelFactory, module.WhisperModel)


def transcribe_wav(
    model: WhisperModel, content: bytes, language: str
) -> tuple[str, str]:
    """VAD 过滤静音后消费全部识别段，返回最终文本与语言.

    Args:
        model: 已加载的识别模型.
        content: 已校验的 PCM WAV 内容.
        language: 识别语言代码.

    Returns:
        去除首尾空白的最终文本，以及引擎报告的语言.
    """
    segments, info = model.transcribe(
        io.BytesIO(content),
        language=language,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    # 识别段是惰性生成器，必须在推理所在线程内全部消费。
    text = "".join(segment.text for segment in segments).strip()
    return text, info.language
