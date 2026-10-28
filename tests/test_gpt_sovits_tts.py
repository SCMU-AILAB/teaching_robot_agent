# tests/test_gpt_sovits_tts.py
"""GPT-SoVITS 合成适配器：请求内容、音频校验、错误、超时与生命周期测试."""

import asyncio
import json
import os
import unittest
from typing import cast
from unittest.mock import patch

import httpx

from app.audio import build_tts
from providers.audio.wav import PcmAudio, encode_wav, read_wav
from providers.tts.gpt_sovits import GPTSoVITSProvider, VoiceProfile
from providers.tts.system import SystemTTSProvider
from storage.evidence import EvidenceStore

WAV = encode_wav(PcmAudio(b"\0\1" * 3200, 32000, 1))
VOICE = VoiceProfile("/home/qwq/opt/GPT-SoVITS/refs/ref.wav", "参考音频原文。")


def make_provider(
    handler: httpx.MockTransport, store: EvidenceStore | None = None
) -> tuple[GPTSoVITSProvider, EvidenceStore]:
    """用模拟传输构造适配器."""
    store = store or EvidenceStore()
    provider = GPTSoVITSProvider(
        store,
        "http://127.0.0.1:9880",
        {"robot": VOICE},
        "robot",
        _transport=handler,
    )
    return provider, store


class GPTSoVITSProviderTest(unittest.IsolatedAsyncioTestCase):
    """GPTSoVITSProvider 行为."""

    async def test_synthesize_posts_profile_and_registers_wav(self) -> None:
        """请求体包含文本与参考音频配置，返回的 WAV 登记为证据."""
        requests: list[dict[str, object]] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(cast(dict[str, object], json.loads(request.content)))
            self.assertEqual(request.url.path, "/tts")
            return httpx.Response(
                200, content=WAV, headers={"content-type": "audio/wav"}
            )

        provider, store = make_provider(httpx.MockTransport(handle))
        await provider.start()
        audio = await provider.synthesize("热水壶很烫，不要碰。", None, 10)
        await provider.close()
        self.assertEqual(await read_wav(store, audio), WAV)
        self.assertEqual(audio.sample_rate_hz, 32000)
        body = requests[0]
        self.assertEqual(body["text"], "热水壶很烫，不要碰。")
        self.assertEqual(body["ref_audio_path"], VOICE.ref_audio_path)
        self.assertEqual(body["prompt_text"], VOICE.prompt_text)
        self.assertEqual((body["text_lang"], body["prompt_lang"]), ("zh", "zh"))
        self.assertEqual(body["media_type"], "wav")
        self.assertIs(body["streaming_mode"], False)

    async def test_warmup_synthesizes_once_and_tolerates_failure(self) -> None:
        """启动预热合成一句并释放音频；服务不可用时只记日志、不阻止启动."""
        calls: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            calls.append(cast(str, json.loads(request.content)["text"]))
            return httpx.Response(200, content=WAV)

        store = EvidenceStore()
        provider = GPTSoVITSProvider(
            store,
            "http://127.0.0.1:9880",
            {"robot": VOICE},
            "robot",
            _transport=httpx.MockTransport(handle),
            _warmup_timeout_s=5,
        )
        await provider.start()
        await provider.start()
        self.assertEqual(calls, ["你好。"])
        self.assertEqual(store.used_bytes, 0)
        await provider.close()

        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        down = GPTSoVITSProvider(
            EvidenceStore(),
            "http://127.0.0.1:9880",
            {"robot": VOICE},
            "robot",
            _transport=httpx.MockTransport(refuse),
            _warmup_timeout_s=5,
        )
        with self.assertLogs("providers.tts.gpt_sovits", "WARNING"):
            await down.start()
        await down.close()

    async def test_server_error_and_invalid_audio_are_rejected(self) -> None:
        """HTTP 错误带上服务端说明，非 WAV 内容不登记."""
        responses = [
            httpx.Response(400, json={"message": "ref_audio_path is required"}),
            httpx.Response(200, content=b"not a wav"),
        ]
        provider, store = make_provider(httpx.MockTransport(lambda _: responses.pop(0)))
        await provider.start()
        with self.assertRaisesRegex(RuntimeError, "ref_audio_path is required"):
            _ = await provider.synthesize("你好", None, 5)
        with self.assertRaises(ValueError):
            _ = await provider.synthesize("你好", None, 5)
        await provider.close()
        self.assertEqual(store.used_bytes, 0)

    async def test_timeout_raises_and_unknown_voice_is_refused(self) -> None:
        """超时抛出 TimeoutError；未配置的音色在发请求前拒绝."""

        async def slow(_: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return httpx.Response(200, content=WAV)

        provider, _ = make_provider(httpx.MockTransport(slow))
        await provider.start()
        with self.assertRaises(TimeoutError):
            _ = await provider.synthesize("你好", None, 0.05)
        with self.assertRaises(ValueError):
            _ = await provider.synthesize("你好", "unknown", 5)
        await provider.close()

    async def test_input_validation(self) -> None:
        """空文本、超长文本与非正期限均拒绝."""
        provider, _ = make_provider(httpx.MockTransport(lambda _: httpx.Response(200)))
        await provider.start()
        for text, timeout in (("", 5.0), ("长" * 2001, 5.0), ("你好", 0.0)):
            with (
                self.subTest(text=text[:5], timeout=timeout),
                self.assertRaises(ValueError),
            ):
                _ = await provider.synthesize(text, None, timeout)
        await provider.close()

    async def test_lifecycle_requires_start_and_close_is_idempotent(self) -> None:
        """启动前和关闭后都拒绝合成，重复关闭安全."""
        provider, _ = make_provider(httpx.MockTransport(lambda _: httpx.Response(200)))
        with self.assertRaises(RuntimeError):
            _ = await provider.synthesize("你好", None, 5)
        await provider.start()
        await provider.close()
        await provider.close()
        with self.assertRaises(RuntimeError):
            _ = await provider.synthesize("你好", None, 5)

    def test_construction_validates_configuration(self) -> None:
        """默认音色必须存在，地址必须是 http(s)."""
        store = EvidenceStore()
        with self.assertRaises(ValueError):
            _ = GPTSoVITSProvider(store, "http://127.0.0.1:9880", {"a": VOICE}, "b")
        with self.assertRaises(ValueError):
            _ = GPTSoVITSProvider(store, "ftp://host", {"a": VOICE}, "a")


class BuildTTSTest(unittest.TestCase):
    """SPEECH_TTS_ENGINE 装配选择."""

    def test_gpt_sovits_engine_registers_lifecycle(self) -> None:
        """选择 gpt-sovits 时返回该适配器并登记为生命周期资源."""
        env = {
            "SPEECH_TTS_ENGINE": "gpt-sovits",
            "SPEECH_GPTSOVITS_REF_AUDIO": "/srv/ref.wav",
            "SPEECH_GPTSOVITS_PROMPT_TEXT": "参考原文。",
        }
        with patch.dict(os.environ, env):
            tts, lifecycle = build_tts(EvidenceStore())
        self.assertIsInstance(tts, GPTSoVITSProvider)
        self.assertEqual(lifecycle, (tts,))

    def test_gpt_sovits_requires_reference_audio(self) -> None:
        """缺少参考音频或原文时直接报错，不退回系统合成."""
        env = {"SPEECH_TTS_ENGINE": "gpt-sovits", "SPEECH_GPTSOVITS_REF_AUDIO": ""}
        with (
            patch.dict(os.environ, env),
            self.assertRaisesRegex(ValueError, "REF_AUDIO"),
        ):
            _ = build_tts(EvidenceStore())

    def test_system_engine_has_no_lifecycle(self) -> None:
        """系统合成引擎无需启动资源."""
        with patch.dict(os.environ, {"SPEECH_TTS_ENGINE": "say"}):
            tts, lifecycle = build_tts(EvidenceStore())
        self.assertIsInstance(tts, SystemTTSProvider)
        self.assertEqual(lifecycle, ())


if __name__ == "__main__":
    _ = unittest.main()
