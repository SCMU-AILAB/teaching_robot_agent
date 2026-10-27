# providers/asr/whisper_engine.py
"""faster-whisper 调用边界：子进程与常驻识别共用的类型和转写步骤."""

import io
from collections.abc import Iterable
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from domain.validation import nonempty_string


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


class WhisperEngine:
    """常驻识别使用的 faster-whisper 引擎，构造期间不导入可选依赖."""

    def __init__(
        self,
        _model_path: str,
        _language: str = "zh",
        _device: str = "cpu",
        _compute_type: str = "int8",
        *,
        _model_factory: WhisperModelFactory | None = None,
    ) -> None:
        """保存模型配置.

        Args:
            _model_path: 已下载的本地模型目录，须包含 model.bin.
            _language: 识别语言代码.
            _device: cpu 或 cuda.
            _compute_type: 引擎计算精度，例如 int8 或 float16.
            _model_factory: 测试注入的模型构造函数；缺省加载时导入 faster-whisper.

        Raises:
            ValueError: 设备或文本参数非法.
        """
        if _device not in {"cpu", "cuda"}:
            raise ValueError("ASR compute device must be cpu or cuda")
        self._model_path: str = nonempty_string(_model_path, "model_path")
        self._language: str = nonempty_string(_language, "language")
        self._device: str = _device
        self._compute_type: str = nonempty_string(_compute_type, "compute_type")
        self._factory: WhisperModelFactory | None = _model_factory
        self._model: WhisperModel | None = None

    @property
    def name(self) -> str:
        """日志使用的引擎名称."""
        return f"whisper/{self._device}/{self._compute_type}"

    def check_files(self) -> None:
        """确认模型目录包含 model.bin."""
        if not (Path(self._model_path) / "model.bin").is_file():
            raise ValueError("Set SPEECH_ASR_MODEL_PATH to a downloaded Whisper model")

    def load(self) -> None:
        """只从本地目录加载模型."""
        factory = self._factory or load_faster_whisper()
        self._model = factory(
            self._model_path,
            device=self._device,
            compute_type=self._compute_type,
            local_files_only=True,
            cpu_threads=4,
        )

    def recognize(self, content: bytes) -> tuple[str, str]:
        """识别已校验的 WAV."""
        if self._model is None:
            raise RuntimeError("Whisper model is not loaded")
        return transcribe_wav(self._model, content, self._language)

    def unload(self) -> None:
        """释放模型引用."""
        self._model = None
