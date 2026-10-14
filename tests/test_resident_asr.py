# tests/test_resident_asr.py
"""验证常驻 ASR 模型的加载、串行识别、取消等待与关闭."""

import asyncio
import io
import os
import tempfile
import threading
import time
import unittest
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import override
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.audio import AudioAdapters, build_asr_engine, build_real_audio
from app.robot_application import RobotApplication
from domain.services import AudioReference
from perception.simulated import SimulatedPerception
from providers.asr.resident import ResidentASRProvider
from providers.asr.sensevoice_engine import SenseVoiceEngine
from providers.asr.whisper_engine import WhisperEngine, WhisperModel
from providers.audio.mock import MockAudioRecorder
from providers.audio.player import MockAudioPlayer
from providers.audio.wav import PcmAudio, encode_wav, save_wav
from providers.tts.mock import MockTTSProvider
from robot.simulated import SimulatedAdapter
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


@dataclass
class FakeSegment:
    """假识别段."""

    text: str


@dataclass
class FakeInfo:
    """假语言信息."""

    language: str


class FakeModel:
    """可阻塞的假模型，记录调用并检测并发推理."""

    def __init__(self, _text: str = " 那插座呢 ") -> None:
        """初始化阻塞开关与计数."""
        self.text: str = _text
        self.calls: int = 0
        self.active: int = 0
        self.max_active: int = 0
        self.started: threading.Event = threading.Event()
        self.release: threading.Event = threading.Event()
        self.release.set()
        self.finished: threading.Event = threading.Event()

    def transcribe(
        self,
        audio: io.BytesIO,
        *,
        language: str,
        beam_size: int,
        vad_filter: bool,
        condition_on_previous_text: bool,
    ) -> tuple[Iterable[FakeSegment], FakeInfo]:
        """在调用线程中阻塞，模拟原生推理不可中断."""
        _ = (audio, beam_size, vad_filter, condition_on_previous_text)
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            _ = self.release.wait(5)
        finally:
            self.active -= 1
            self.finished.set()
        return [FakeSegment(self.text)], FakeInfo(language)


class FakeFactory:
    """记录加载次数和参数的假模型构造函数."""

    def __init__(self, _model: FakeModel) -> None:
        """注入要返回的模型."""
        self.model: FakeModel = _model
        self.loads: int = 0
        self.kwargs: dict[str, object] = {}

    def __call__(
        self,
        model_size_or_path: str,
        *,
        device: str,
        compute_type: str,
        local_files_only: bool,
        cpu_threads: int,
    ) -> WhisperModel:
        """返回同一个假模型."""
        self.loads += 1
        self.kwargs = {
            "path": model_size_or_path,
            "device": device,
            "compute_type": compute_type,
            "local_files_only": local_files_only,
            "cpu_threads": cpu_threads,
        }
        return self.model


class ResidentASRTests(unittest.IsolatedAsyncioTestCase):
    """常驻模型由应用生命周期管理，识别期间不重新加载."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """初始化每个用例的独立状态."""
        super().__init__(_method_name)
        self.store: EvidenceStore = EvidenceStore()
        self.model: FakeModel = FakeModel()
        self.factory: FakeFactory = FakeFactory(self.model)
        self.model_dir: Path = Path()

    @override
    async def asyncSetUp(self) -> None:
        """准备含 model.bin 的模型目录."""
        directory = tempfile.TemporaryDirectory(prefix="resident-asr-")
        self.addCleanup(directory.cleanup)
        self.model_dir = Path(directory.name)
        _ = (self.model_dir / "model.bin").write_bytes(b"fake")

    def provider(self) -> ResidentASRProvider:
        """构造使用假 Whisper 模型的常驻识别器."""
        return ResidentASRProvider(
            self.store, WhisperEngine(str(self.model_dir), _model_factory=self.factory)
        )

    async def audio(self) -> AudioReference:
        """登记一段一秒的静音 WAV."""
        content = encode_wav(PcmAudio(b"\0\0" * 16000, 16000, 1))
        return await save_wav(self.store, content, time.time())

    async def test_loads_once_and_reuses_model(self) -> None:
        """启动只加载一次，多次识别复用同一模型且只用本地文件."""
        asr = self.provider()
        await asr.start()
        await asr.start()
        first = await asr.transcribe(await self.audio(), 5)
        second = await asr.transcribe(await self.audio(), 5)
        await asr.close()
        self.assertEqual(self.factory.loads, 1)
        self.assertEqual(self.model.calls, 2)
        self.assertEqual(first.text, "那插座呢")
        self.assertEqual(first.language, "zh")
        self.assertTrue(first.is_final)
        self.assertEqual(second.text, "那插座呢")
        self.assertEqual(self.factory.kwargs["local_files_only"], True)
        self.assertEqual(self.factory.kwargs["device"], "cpu")
        self.assertEqual(self.factory.kwargs["compute_type"], "int8")

    async def test_transcribe_requires_start_and_rejects_after_close(self) -> None:
        """未加载或已关闭时明确失败，不临时加载模型."""
        asr = self.provider()
        with self.assertRaisesRegex(RuntimeError, "not loaded"):
            _ = await asr.transcribe(await self.audio(), 5)
        await asr.start()
        await asr.close()
        await asr.close()
        with self.assertRaisesRegex(RuntimeError, "not loaded"):
            _ = await asr.transcribe(await self.audio(), 5)
        self.assertEqual(self.model.calls, 0)

    async def test_missing_model_directory_fails_start(self) -> None:
        """模型目录缺少 model.bin 时启动失败，不自动下载."""
        (self.model_dir / "model.bin").unlink()
        with self.assertRaisesRegex(ValueError, "SPEECH_ASR_MODEL_PATH"):
            await self.provider().start()
        self.assertEqual(self.factory.loads, 0)

    async def test_invalid_configuration_rejected(self) -> None:
        """设备和期限参数先校验."""
        with self.assertRaises(ValueError):
            _ = WhisperEngine(str(self.model_dir), _device="tpu")
        asr = self.provider()
        await asr.start()
        for timeout in (0.0, -1.0, float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                _ = await asr.transcribe(await self.audio(), timeout)
        await asr.close()

    async def test_concurrent_requests_are_serialized(self) -> None:
        """同一模型不并发推理."""
        asr = self.provider()
        await asr.start()
        self.model.release.clear()
        first = asyncio.create_task(asr.transcribe(await self.audio(), 5))
        second = asyncio.create_task(asr.transcribe(await self.audio(), 5))
        _ = await asyncio.to_thread(self.model.started.wait, 2)
        await asyncio.sleep(0.05)
        self.model.release.set()
        _ = await asyncio.gather(first, second)
        await asr.close()
        self.assertEqual(self.model.max_active, 1)
        self.assertEqual(self.model.calls, 2)

    async def test_cancel_waits_for_inference_to_finish(self) -> None:
        """取消识别时等原生推理真正结束后才传播取消，结果被丢弃."""
        asr = self.provider()
        await asr.start()
        self.model.release.clear()
        task = asyncio.create_task(asr.transcribe(await self.audio(), 5))
        _ = await asyncio.to_thread(self.model.started.wait, 2)
        _ = task.cancel()
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())  # 推理仍在线程中，取消不能提前返回
        self.model.release.set()
        with self.assertRaises(asyncio.CancelledError):
            _ = await task
        self.assertTrue(self.model.finished.is_set())
        self.assertEqual(self.model.active, 0)
        await asr.close()

    async def test_timeout_waits_then_raises(self) -> None:
        """超时后同样等待推理结束，再抛出超时."""
        asr = self.provider()
        await asr.start()
        self.model.release.clear()
        task = asyncio.create_task(asr.transcribe(await self.audio(), 0.1))
        _ = await asyncio.to_thread(self.model.started.wait, 2)
        await asyncio.sleep(0.2)
        self.assertFalse(task.done())
        self.model.release.set()
        with self.assertRaises(TimeoutError):
            _ = await task
        self.assertEqual(self.model.active, 0)
        await asr.close()

    async def test_close_waits_for_running_inference(self) -> None:
        """关闭在推理结束后才释放模型，之后的识别被拒绝."""
        asr = self.provider()
        await asr.start()
        self.model.release.clear()
        task = asyncio.create_task(asr.transcribe(await self.audio(), 5))
        _ = await asyncio.to_thread(self.model.started.wait, 2)
        closing = asyncio.create_task(asr.close())
        await asyncio.sleep(0.05)
        self.assertFalse(closing.done())
        self.model.release.set()
        await closing
        self.assertEqual((await task).text, "那插座呢")
        with self.assertRaisesRegex(RuntimeError, "not loaded"):
            _ = await asr.transcribe(await self.audio(), 5)


class ResidentASRApplicationTests(unittest.IsolatedAsyncioTestCase):
    """常驻识别随 RobotApplication 启停，录音转写进入主链路."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """初始化每个用例的独立状态."""
        super().__init__(_method_name)
        self.model_dir: Path = Path()

    @override
    async def asyncSetUp(self) -> None:
        """准备含 model.bin 的模型目录."""
        directory = tempfile.TemporaryDirectory(prefix="resident-asr-app-")
        self.addCleanup(directory.cleanup)
        self.model_dir = Path(directory.name)
        _ = (self.model_dir / "model.bin").write_bytes(b"fake")

    def build(
        self, factory: FakeFactory
    ) -> tuple[RobotApplication, ResidentASRProvider]:
        """装配带常驻识别的应用，设备与合成为模拟实现."""
        evidence = EvidenceStore()
        device = AudioDeviceLease()
        asr = ResidentASRProvider(
            evidence, WhisperEngine(str(self.model_dir), _model_factory=factory)
        )
        audio = AudioAdapters(
            MockAudioRecorder(evidence, device),
            asr,
            MockTTSProvider(evidence),
            MockAudioPlayer(evidence, device),
            simulated=True,
            lifecycle=(asr,),
        )
        chat = FakeMessagesListChatModel(responses=[AIMessage("插座不能用手碰。")])
        app = RobotApplication(
            SimulatedAdapter(),
            SimulatedPerception(evidence),
            chat,
            _audio=audio,
            _evidence=evidence,
        )
        return app, asr

    async def test_app_loads_model_once_and_answers_speech(self) -> None:
        """启动加载一次模型，录音识别后 Agent 回答，关闭后释放模型."""
        model = FakeModel(" 那插座呢 ")
        factory = FakeFactory(model)
        app, asr = self.build(factory)
        try:
            await app.start()
            self.assertEqual(factory.loads, 1)
            task = await app.create_task("语音对话")
            await app.start_recording(task.task_id, "rec-1", 5, 5)
            item = await app.finish_recording("rec-1")
            assert item is not None
            self.assertEqual(item.text, "那插座呢")
            self.assertEqual(item.source, "speech")
            with patch.object(
                FakeMessagesListChatModel,
                "bind_tools",
                return_value=FakeMessagesListChatModel(
                    responses=[AIMessage("插座不能用手碰。")]
                ),
            ):
                reply = await app.process_next()
            self.assertEqual(reply.text, "插座不能用手碰。")
            self.assertEqual(factory.loads, 1)
        finally:
            await app.close()
        with self.assertRaisesRegex(RuntimeError, "not loaded"):
            _ = await asr.transcribe(
                await save_wav(
                    EvidenceStore(),
                    encode_wav(PcmAudio(b"\0\0" * 1600, 16000, 1)),
                    time.time(),
                ),
                5,
            )

    async def test_missing_model_fails_app_start(self) -> None:
        """模型缺失时应用启动明确失败，不退回模拟识别."""
        (self.model_dir / "model.bin").unlink()
        model = FakeModel()
        factory = FakeFactory(model)
        app, _ = self.build(factory)
        try:
            with self.assertRaisesRegex(ValueError, "SPEECH_ASR_MODEL_PATH"):
                await app.start()
        finally:
            await app.close()
        self.assertEqual(factory.loads, 0)

    def test_real_audio_requires_model_path(self) -> None:
        """真实语音装配缺少模型路径时直接报错."""
        with patch.dict(os.environ, {"SPEECH_ASR_MODEL_PATH": ""}):
            with self.assertRaisesRegex(ValueError, "SPEECH_ASR_MODEL_PATH"):
                _ = build_real_audio(EvidenceStore())

    def test_real_audio_defaults_to_sensevoice(self) -> None:
        """真实语音默认装配常驻 SenseVoice，登记为生命周期资源且标记非模拟."""
        with patch.dict(
            os.environ,
            {"SPEECH_ASR_MODEL_PATH": str(self.model_dir), "SPEECH_ASR_ENGINE": ""},
        ):
            _ = os.environ.pop("SPEECH_ASR_ENGINE")
            audio = build_real_audio(EvidenceStore())
        self.assertFalse(audio.simulated)
        assert isinstance(audio.asr, ResidentASRProvider)
        self.assertIsInstance(audio.asr.engine, SenseVoiceEngine)
        self.assertEqual(audio.lifecycle, (audio.asr,))

    def test_engine_selection(self) -> None:
        """SPEECH_ASR_ENGINE 可切换 Whisper，未知引擎直接报错."""
        with patch.dict(
            os.environ,
            {
                "SPEECH_ASR_MODEL_PATH": str(self.model_dir),
                "SPEECH_ASR_ENGINE": "whisper",
            },
        ):
            self.assertIsInstance(build_asr_engine(), WhisperEngine)
        with patch.dict(
            os.environ,
            {
                "SPEECH_ASR_MODEL_PATH": str(self.model_dir),
                "SPEECH_ASR_ENGINE": "cloud",
            },
        ):
            with self.assertRaisesRegex(ValueError, "SPEECH_ASR_ENGINE"):
                _ = build_asr_engine()


if __name__ == "__main__":
    _ = unittest.main()
