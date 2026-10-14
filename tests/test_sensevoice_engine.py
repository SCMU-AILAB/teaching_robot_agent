# tests/test_sensevoice_engine.py
"""验证 SenseVoice 引擎的模型文件检查、采样转换与常驻识别接入."""

import tempfile
import time
import unittest
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import override

from providers.asr.resident import ResidentASRProvider
from providers.asr.sensevoice_engine import (
    SenseVoiceEngine,
    SenseVoiceRecognizer,
    SenseVoiceStream,
    pcm_to_mono_floats,
)
from providers.audio.wav import PcmAudio, encode_wav, save_wav
from storage.evidence import EvidenceStore


@dataclass
class FakeResult:
    """假识别结果."""

    text: str


@dataclass
class FakeStream:
    """记录输入采样的假识别流."""

    result: FakeResult = field(default_factory=lambda: FakeResult(""))
    sample_rate: int = 0
    samples: list[float] = field(default_factory=list)

    def accept_waveform(self, sample_rate: int, waveform: Sequence[float]) -> None:
        """记录采样率和采样."""
        self.sample_rate = sample_rate
        self.samples = list(waveform)


class FakeRecognizer:
    """返回固定文本的假识别器."""

    def __init__(self, _text: str) -> None:
        """注入识别文本."""
        self.text: str = _text
        self.streams: list[FakeStream] = []

    def create_stream(self) -> SenseVoiceStream:
        """创建并记录识别流."""
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def decode_stream(self, stream: SenseVoiceStream) -> None:
        """写入固定识别结果."""
        assert isinstance(stream, FakeStream)
        stream.result = FakeResult(self.text)


class FakeFactory:
    """记录构造参数的假识别器工厂."""

    def __init__(self, _recognizer: FakeRecognizer) -> None:
        """注入要返回的识别器."""
        self.recognizer: FakeRecognizer = _recognizer
        self.kwargs: dict[str, object] = {}
        self.loads: int = 0

    def __call__(
        self,
        *,
        model: str,
        tokens: str,
        num_threads: int,
        use_itn: bool,
        language: str,
    ) -> SenseVoiceRecognizer:
        """返回同一个假识别器."""
        self.loads += 1
        self.kwargs = {
            "model": model,
            "tokens": tokens,
            "num_threads": num_threads,
            "use_itn": use_itn,
            "language": language,
        }
        return self.recognizer


class SenseVoiceEngineTests(unittest.IsolatedAsyncioTestCase):
    """SenseVoice 只加载本地 ONNX 文件，输入归一化单声道采样."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """初始化每个用例的独立状态."""
        super().__init__(_method_name)
        self.model_dir: Path = Path()
        self.recognizer: FakeRecognizer = FakeRecognizer("  热水壶能碰吗？ ")
        self.factory: FakeFactory = FakeFactory(self.recognizer)

    @override
    async def asyncSetUp(self) -> None:
        """准备含 int8 模型与词表的目录."""
        directory = tempfile.TemporaryDirectory(prefix="sensevoice-")
        self.addCleanup(directory.cleanup)
        self.model_dir = Path(directory.name)
        _ = (self.model_dir / "model.int8.onnx").write_bytes(b"fake")
        _ = (self.model_dir / "tokens.txt").write_text("a 0\n", encoding="utf-8")

    def engine(self) -> SenseVoiceEngine:
        """构造使用假识别器的引擎."""
        return SenseVoiceEngine(str(self.model_dir), _recognizer_factory=self.factory)

    def test_check_files_requires_model_and_tokens(self) -> None:
        """缺少模型或词表时明确失败；只有 model.onnx 也可接受."""
        self.engine().check_files()
        (self.model_dir / "tokens.txt").unlink()
        with self.assertRaisesRegex(ValueError, "SPEECH_ASR_MODEL_PATH"):
            self.engine().check_files()
        _ = (self.model_dir / "tokens.txt").write_text("a 0\n", encoding="utf-8")
        _ = (self.model_dir / "model.int8.onnx").rename(self.model_dir / "model.onnx")
        self.engine().check_files()
        (self.model_dir / "model.onnx").unlink()
        with self.assertRaisesRegex(ValueError, "SPEECH_ASR_MODEL_PATH"):
            self.engine().check_files()

    def test_load_prefers_int8_and_enables_itn(self) -> None:
        """加载优先使用 int8 模型，启用逆文本规整并固定语言."""
        _ = (self.model_dir / "model.onnx").write_bytes(b"fake")
        engine = self.engine()
        engine.load()
        self.assertEqual(
            self.factory.kwargs["model"], str(self.model_dir / "model.int8.onnx")
        )
        self.assertEqual(
            self.factory.kwargs["tokens"], str(self.model_dir / "tokens.txt")
        )
        self.assertEqual(self.factory.kwargs["use_itn"], True)
        self.assertEqual(self.factory.kwargs["language"], "zh")
        self.assertEqual(self.factory.kwargs["num_threads"], 4)

    def test_invalid_configuration_rejected(self) -> None:
        """不支持的语言和非正线程数被拒绝."""
        with self.assertRaises(ValueError):
            _ = SenseVoiceEngine(str(self.model_dir), "fr")
        with self.assertRaises(ValueError):
            _ = SenseVoiceEngine(str(self.model_dir), _num_threads=0)

    def test_recognize_requires_load(self) -> None:
        """未加载时识别明确失败."""
        content = encode_wav(PcmAudio(b"\0\0" * 160, 16000, 1))
        with self.assertRaisesRegex(RuntimeError, "not loaded"):
            _ = self.engine().recognize(content)

    def test_recognize_passes_normalized_mono_samples(self) -> None:
        """单声道采样按 32768 归一化，结果去除首尾空白."""
        engine = self.engine()
        engine.load()
        frames = (16384).to_bytes(2, "little", signed=True) + (-32768).to_bytes(
            2, "little", signed=True
        )
        content = encode_wav(PcmAudio(frames * 4000, 16000, 1))
        text, language = engine.recognize(content)
        self.assertEqual((text, language), ("热水壶能碰吗？", "zh"))
        stream = self.recognizer.streams[-1]
        self.assertEqual(stream.sample_rate, 16000)
        self.assertEqual(stream.samples[:2], [0.5, -1.0])
        self.assertEqual(len(stream.samples), 8000)

    def test_stereo_is_averaged_to_mono(self) -> None:
        """双声道按左右平均转为单声道."""
        left = (16384).to_bytes(2, "little", signed=True)
        right = (0).to_bytes(2, "little", signed=True)
        self.assertEqual(pcm_to_mono_floats(left + right, 2), [0.25])

    async def test_resident_provider_uses_engine(self) -> None:
        """常驻识别器经 SenseVoice 引擎识别已登记音频，只加载一次."""
        store = EvidenceStore()
        asr = ResidentASRProvider(store, self.engine())
        await asr.start()
        await asr.start()
        audio = await save_wav(
            store, encode_wav(PcmAudio(b"\0\0" * 16000, 16000, 1)), time.time()
        )
        result = await asr.transcribe(audio, 5)
        await asr.close()
        self.assertEqual(self.factory.loads, 1)
        self.assertEqual(result.text, "热水壶能碰吗？")
        self.assertTrue(result.is_final)


if __name__ == "__main__":
    _ = unittest.main()
