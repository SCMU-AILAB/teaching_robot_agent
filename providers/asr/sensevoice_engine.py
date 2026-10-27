# providers/asr/sensevoice_engine.py
"""SenseVoice 中文离线识别引擎，经 sherpa-onnx 在 CPU 上运行."""

import array
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from domain.validation import nonempty_string, positive_int
from providers.audio.wav import decode_wav


class SenseVoiceResult(Protocol):
    """一次识别的输出."""

    @property
    def text(self) -> str:
        """识别文本."""
        ...


class SenseVoiceStream(Protocol):
    """一段离线音频的识别流."""

    @property
    def result(self) -> SenseVoiceResult:
        """解码后的结果."""
        ...

    def accept_waveform(self, sample_rate: int, waveform: Sequence[float]) -> None:
        """输入归一化到 [-1, 1] 的单声道采样."""
        ...


class SenseVoiceRecognizer(Protocol):
    """仅声明实际使用的 sherpa-onnx 离线识别器边界."""

    def create_stream(self) -> SenseVoiceStream:
        """创建识别流."""
        ...

    def decode_stream(self, stream: SenseVoiceStream) -> None:
        """同步解码一段音频."""
        ...


class SenseVoiceFactory(Protocol):
    """sherpa-onnx SenseVoice 构造函数的类型边界."""

    def __call__(
        self,
        *,
        model: str,
        tokens: str,
        num_threads: int,
        use_itn: bool,
        language: str,
    ) -> SenseVoiceRecognizer:
        """从本地 ONNX 模型构造识别器."""
        ...


class _OfflineRecognizerClass(Protocol):
    """sherpa-onnx 离线识别器类中实际使用的构造入口."""

    @property
    def from_sense_voice(self) -> SenseVoiceFactory:
        """SenseVoice 模型构造函数."""
        ...


def load_sense_voice() -> SenseVoiceFactory:
    """调用时才导入可选依赖，未安装语音依赖的应用仍可导入本模块."""
    module = import_module("sherpa_onnx")
    # 外部类名不符合本项目命名规则，经模块字典取出后收窄为实际使用的协议。
    recognizer_class = cast(_OfflineRecognizerClass, vars(module)["OfflineRecognizer"])
    return recognizer_class.from_sense_voice


def pcm_to_mono_floats(frames: bytes, channels: int) -> list[float]:
    """把十六位 PCM 转为归一化单声道采样，双声道取平均."""
    samples = array.array("h", frames)
    if channels == 1:
        return [value / 32768 for value in samples]
    return [
        (samples[index] + samples[index + 1]) / 65536
        for index in range(0, len(samples) - 1, 2)
    ]


class SenseVoiceEngine:
    """常驻识别使用的 SenseVoice 引擎，构造期间不导入 sherpa-onnx."""

    def __init__(
        self,
        _model_path: str,
        _language: str = "zh",
        _num_threads: int = 4,
        *,
        _recognizer_factory: SenseVoiceFactory | None = None,
    ) -> None:
        """保存模型配置.

        Args:
            _model_path: 本地模型目录，须包含 model.int8.onnx（或 model.onnx）与 tokens.txt.
            _language: 识别语言代码，SenseVoice 支持 zh、en、ja、ko、yue.
            _num_threads: 推理线程数.
            _recognizer_factory: 测试注入的识别器构造函数；缺省加载时导入 sherpa-onnx.

        Raises:
            ValueError: 语言或线程数非法.
        """
        language = nonempty_string(_language, "language")
        if language not in {"zh", "en", "ja", "ko", "yue"}:
            raise ValueError("SenseVoice language must be zh, en, ja, ko or yue")
        self._model_path: Path = Path(nonempty_string(_model_path, "model_path"))
        self._language: str = language
        self._num_threads: int = positive_int(_num_threads, "num_threads")
        self._factory: SenseVoiceFactory | None = _recognizer_factory
        self._recognizer: SenseVoiceRecognizer | None = None

    @property
    def name(self) -> str:
        """日志使用的引擎名称."""
        return f"sensevoice/cpu/{self._num_threads}"

    def _model_file(self) -> Path:
        """优先使用 int8 量化模型."""
        for name in ("model.int8.onnx", "model.onnx"):
            if (self._model_path / name).is_file():
                return self._model_path / name
        raise ValueError("Set SPEECH_ASR_MODEL_PATH to a downloaded SenseVoice model")

    def check_files(self) -> None:
        """确认模型与词表文件存在."""
        _ = self._model_file()
        if not (self._model_path / "tokens.txt").is_file():
            raise ValueError(
                "Set SPEECH_ASR_MODEL_PATH to a downloaded SenseVoice model"
            )

    def load(self) -> None:
        """从本地 ONNX 文件构造识别器；启用逆文本规整输出标点和数字."""
        factory = self._factory or load_sense_voice()
        self._recognizer = factory(
            model=str(self._model_file()),
            tokens=str(self._model_path / "tokens.txt"),
            num_threads=self._num_threads,
            use_itn=True,
            language=self._language,
        )

    def recognize(self, content: bytes) -> tuple[str, str]:
        """识别已校验的 WAV，返回去除首尾空白的文本与配置的语言."""
        if self._recognizer is None:
            raise RuntimeError("SenseVoice model is not loaded")
        audio = decode_wav(content)
        stream = self._recognizer.create_stream()
        stream.accept_waveform(
            audio.sample_rate, pcm_to_mono_floats(audio.frames, audio.channels)
        )
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip(), self._language

    def unload(self) -> None:
        """释放识别器引用."""
        self._recognizer = None
