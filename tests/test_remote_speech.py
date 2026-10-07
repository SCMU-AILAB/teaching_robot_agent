# tests/test_remote_speech.py
"""验证客户端与无设备服务器的传输、断线、期限和取消回收."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest.mock import patch

from providers.asr.remote import RemoteASRProvider
from providers.audio.wav import PcmAudio, encode_wav
from providers.tts.piper import PiperTTSProvider
from providers.tts.remote import RemoteTTSProvider
from speech.config import SpeechConfig
from speech.remote import SpeechServer, SSHSpeechTransport
from speech.remote_worker import RemoteRequest, serve
from storage.evidence import EvidenceStore


class RemoteSpeechTests(unittest.IsolatedAsyncioTestCase):
    """本地启动服务器协议替身，不依赖公网、真实模型或音频设备."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """初始化每个测试的独立状态."""
        super().__init__(_method_name)
        self.root: Path = Path()
        self.store: EvidenceStore = EvidenceStore()
        self.baseline: set[asyncio.Task[object]] = set()

    @override
    async def asyncSetUp(self) -> None:
        """建立进程标记目录并记录后台任务基线."""
        directory = tempfile.TemporaryDirectory(prefix="remote-speech-test-")
        self.root = Path(directory.name)
        self.addCleanup(directory.cleanup)
        self.baseline = set(asyncio.all_tasks())

    def transport(self, scenario: str = "normal") -> SSHSpeechTransport:
        """仅替换 SSH 连接边界，其余字节协议和关闭确认均运行真实实现."""
        transport = SSHSpeechTransport(
            SpeechServer("localhost", "tester", 22, "/srv/speech")
        )
        patcher = patch.object(
            transport,
            "_command",
            (
                sys.executable,
                "-m",
                "tests.remote_speech_worker_fixture",
                scenario,
                str(self.root),
            ),
        )
        _ = patcher.start()
        self.addCleanup(patcher.stop)
        return transport

    def assert_reaped(self) -> None:
        """取消返回之前远端父子进程都必须退出，且本地无遗留等待任务."""
        for marker in self.root.iterdir():
            pid = int(marker.read_text(encoding="ascii"))
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        self.assertFalse(asyncio.all_tasks() - self.baseline - {asyncio.current_task()})

    async def wait_child(self) -> None:
        """等待远端推理子进程真正启动，再发起取消."""
        async with asyncio.timeout(3):
            while not (self.root / "child").exists():
                await asyncio.sleep(0.005)

    async def test_remote_tts_asr_and_empty(self) -> None:
        """服务器返回的 WAV 在本地登记，然后可上传识别，空文本保持为空."""
        transport = self.transport()
        audio = await RemoteTTSProvider(self.store, transport).synthesize(
            "测试", None, 3
        )
        self.assertEqual(audio.duration_s, 0.1)
        self.assertEqual(self.store.get(audio.evidence_id).media_type, "audio/wav")
        result = await RemoteASRProvider(self.store, transport).transcribe(audio, 3)
        self.assertEqual(result.text, "识别成功")
        self.assertTrue(result.is_final)
        result = await RemoteASRProvider(
            self.store, self.transport("empty")
        ).transcribe(audio, 3)
        self.assertEqual(result.text, "")
        self.assert_reaped()

    async def test_cancel_waits_for_remote_process_cleanup(self) -> None:
        """取消识别或合成时都收到远端清理确认，不遗留推理子进程."""
        for operation in ("asr", "tts"):
            with self.subTest(operation=operation):
                child = self.root / "child"
                if child.exists():
                    child.unlink()
                request = asyncio.create_task(
                    self.transport("hang").request(operation, b"test", 10, "zh")
                )
                await self.wait_child()
                _ = request.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    _ = await request
                self.assert_reaped()

    async def test_timeout_also_reaps_remote_job(self) -> None:
        """客户端期限到达后结束远端作业，再抛超时."""
        with self.assertRaises(TimeoutError):
            _ = await self.transport("hang").request("tts", b"test", 0.5, "cmn")
        self.assert_reaped()

    async def test_remote_failure_and_malformed_audio(self) -> None:
        """远端失败和非法媒体不会返回假成功证据."""
        with self.assertRaisesRegex(RuntimeError, "failed"):
            _ = await RemoteTTSProvider(self.store, self.transport("fail")).synthesize(
                "测试", None, 3
            )
        with self.assertRaises(ValueError):
            _ = await RemoteTTSProvider(
                self.store, self.transport("invalid")
            ).synthesize("测试", None, 3)
        self.assert_reaped()

    def reader(self, timeout_s: float = 1) -> asyncio.StreamReader:
        """构造有效请求但保留输入通道打开，用于精确控制 EOF 时序."""
        content = encode_wav(PcmAudio(b"\0\0" * 1600, 16000, 1))
        reader = asyncio.StreamReader()
        reader.feed_data(
            json.dumps(
                {
                    "operation": "asr",
                    "size": len(content),
                    "timeout_s": timeout_s,
                    "option": "zh",
                }
            ).encode()
            + b"\n"
            + content
        )
        return reader

    async def test_disconnect_waits_for_cleanup_and_discards_late_result(self) -> None:
        """断线后即使引擎吞掉取消返回，仍丢弃结果并等其清理完成."""
        entered = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()

        async def late(_request: RemoteRequest) -> bytes:
            """模拟退出前必须等待释放的模型适配器."""
            entered.set()
            try:
                _ = await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                _ = await release.wait()
                return b"late transcript"
            return b""

        reader = self.reader()
        request = asyncio.create_task(serve(reader, late))
        try:
            _ = await entered.wait()
            reader.feed_eof()
            _ = await cleaning.wait()
            self.assertFalse(request.done())
            release.set()
            self.assertEqual(await request, ("cancelled", b""))
        finally:
            release.set()
            _ = await asyncio.gather(request, return_exceptions=True)
        self.assert_reaped()

    async def test_server_deadline_independent_of_client(self) -> None:
        """即便客户端一直保持连接，服务器自身期限也会取消引擎."""
        cleaned = asyncio.Event()

        async def hang(_request: RemoteRequest) -> bytes:
            """验证服务器期限触发清理，而非仅依赖客户端断开."""
            try:
                _ = await asyncio.Event().wait()
                return b""
            finally:
                cleaned.set()

        self.assertEqual(await serve(self.reader(0.01), hang), ("timed_out", b""))
        self.assertTrue(cleaned.is_set())
        self.assert_reaped()

    async def test_invalid_server_requests_never_start_engine(self) -> None:
        """拒绝不认识的操作、超长期限与未定义字段，不调用模型."""
        calls = 0

        async def execute(_request: RemoteRequest) -> bytes:
            """记录实际模型调用次数."""
            nonlocal calls
            calls += 1
            return b""

        for invalid in (
            {"operation": "shell", "size": 1, "timeout_s": 1, "option": "zh"},
            {"operation": "tts", "size": 1, "timeout_s": 301, "option": "zh"},
            {
                "operation": "tts",
                "size": 1,
                "timeout_s": 1,
                "option": "zh",
                "path": "/tmp",
            },
        ):
            reader = asyncio.StreamReader()
            reader.feed_data(json.dumps(invalid).encode() + b"\na")
            self.assertEqual(await serve(reader, execute), ("failed", b""))
        self.assertEqual(calls, 0)

    def test_client_configuration_needs_no_local_model(self) -> None:
        """本地只需设备与 SSH 配置，不检查本地模型或 TTS 安装."""
        with patch.dict(
            os.environ,
            {
                "SPEECH_SSH_HOST": "localhost",
                "SPEECH_SSH_USER": "tester",
                "SPEECH_SSH_PORT": "2222",
                "SPEECH_SERVER_DIRECTORY": "/srv/speech",
            },
            clear=True,
        ):
            config = SpeechConfig.from_env()
        self.assertEqual(config.server.port, 2222)
        self.assertEqual(config.voice, "huayan")
        with self.assertRaises(ValueError):
            _ = SpeechServer("host;command", "tester", 22, "/srv").command()
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            _ = SpeechConfig.from_env()

    async def test_piper_file_generation_and_voice_validation(self) -> None:
        """服务器 Piper 只生成文件，音色不匹配时拒绝而不回退其他引擎."""
        model = self.root / "voice.onnx"
        _ = model.write_bytes(b"model fixture")
        _ = Path(str(model) + ".json").write_text("{}", encoding="utf-8")
        provider = PiperTTSProvider(self.store, str(model))
        outputs: list[Path] = []

        async def synthesize(
            command: tuple[str, ...], content: bytes, timeout_s: float
        ) -> bytes:
            """仅替换神经引擎，保留模型检查、文件校验和证据登记流程."""
            self.assertEqual(command[1:3], ("-m", "piper"))
            self.assertEqual(content, "测试".encode())
            self.assertEqual(timeout_s, 3)
            output = Path(command[-1])
            outputs.append(output)
            _ = output.write_bytes(encode_wav(PcmAudio(b"\0\0" * 1600, 16000, 1)))
            return b""

        with patch("providers.tts.piper.run_process", new=synthesize):
            audio = await provider.synthesize("测试", "huayan", 3)
            self.assertEqual(audio.duration_s, 0.1)
            with self.assertRaises(ValueError):
                _ = await provider.synthesize("测试", "unknown", 3)
        self.assertTrue(all(not output.exists() for output in outputs))
