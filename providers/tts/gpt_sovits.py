# providers/tts/gpt_sovits.py
"""调用 GPT-SoVITS api_v2 合成训练音色，校验并登记 WAV，不直接播放."""

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import httpx

from domain.services import AudioReference
from domain.validation import finite_float, nonempty_string
from providers.audio.wav import save_wav
from storage.evidence import EvidenceStore

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 2000
MAX_AUDIO_BYTES = 32 * 1024 * 1024
LANGUAGES = frozenset({"zh", "en", "ja", "ko", "yue", "auto"})


@dataclass(frozen=True)
class VoiceProfile:
    """一个音色的参考音频配置；参考音频路径位于 GPT-SoVITS 服务所在机器."""

    ref_audio_path: str
    prompt_text: str
    prompt_lang: str = "zh"


class GPTSoVITSProvider:
    """实现 TTSProvider；HTTP 客户端在 start 中创建，close 中释放."""

    def __init__(
        self,
        _evidence: EvidenceStore,
        _base_url: str,
        _voices: Mapping[str, VoiceProfile],
        _default_voice: str,
        *,
        _text_lang: str = "zh",
        _speed_factor: float = 1.0,
        _transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """校验服务地址与音色配置，构造时不发起连接.

        Args:
            _evidence: 合成音频登记位置。
            _base_url: api_v2 服务地址，例如 ``http://127.0.0.1:9880``。
            _voices: 音色名到参考音频配置的映射。
            _default_voice: 未指定 voice_id 时使用的音色。
            _text_lang: 待合成文本的语言。
            _speed_factor: 语速倍率，0.5–2.0。
            _transport: 测试替换用的 HTTP 传输层。

        Raises:
            ValueError: 地址、音色、语言或语速不合法。
        """
        url = nonempty_string(_base_url, "base_url").rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise ValueError("GPT-SoVITS URL must start with http:// or https://")
        if _default_voice not in _voices:
            raise ValueError("Default voice is not configured")
        for profile in _voices.values():
            _ = nonempty_string(profile.ref_audio_path, "ref_audio_path")
            _ = nonempty_string(profile.prompt_text, "prompt_text")
            if profile.prompt_lang not in LANGUAGES:
                raise ValueError("Unsupported prompt language")
        if _text_lang not in LANGUAGES:
            raise ValueError("Unsupported text language")
        speed = finite_float(_speed_factor, "speed_factor")
        if not 0.5 <= speed <= 2.0:
            raise ValueError("speed_factor must be within [0.5, 2.0]")
        self._evidence: EvidenceStore = _evidence
        self._url: str = url
        self._voices: dict[str, VoiceProfile] = dict(_voices)
        self._default_voice: str = _default_voice
        self._text_lang: str = _text_lang
        self._speed: float = speed
        self._transport: httpx.AsyncBaseTransport | None = _transport
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """创建 HTTP 客户端；重复调用复用同一客户端."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._url, transport=self._transport
            )

    async def close(self) -> None:
        """释放 HTTP 客户端，重复调用安全."""
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def synthesize(
        self, text: str, voice_id: str | None, timeout_s: float
    ) -> AudioReference:
        """请求合成并登记返回的 WAV；取消或超时不会登记迟到音频.

        Raises:
            ValueError: 文本、音色或期限不合法，或服务返回的不是有效 WAV。
            RuntimeError: 服务未启动或返回错误状态。
            TimeoutError: 超过期限。
        """
        text = nonempty_string(text, "text")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"TTS text exceeds {MAX_TEXT_CHARS} characters")
        name = self._default_voice if voice_id is None else voice_id
        profile = self._voices.get(name)
        if profile is None:
            raise ValueError(f"Voice {name!r} is not configured")
        timeout = finite_float(timeout_s, "timeout_s")
        if timeout <= 0:
            raise ValueError("TTS timeout must be positive")
        client = self._client
        if client is None:
            raise RuntimeError("GPT-SoVITS provider is not started")
        body = {
            "text": text,
            "text_lang": self._text_lang,
            "ref_audio_path": profile.ref_audio_path,
            "prompt_text": profile.prompt_text,
            "prompt_lang": profile.prompt_lang,
            "text_split_method": "cut5",
            "batch_size": 1,
            "media_type": "wav",
            "streaming_mode": False,
            "speed_factor": self._speed,
        }
        started = time.time()
        async with asyncio.timeout(timeout):
            response = await client.post("/tts", json=body, timeout=timeout)
        if response.status_code != 200:
            raise RuntimeError(
                f"GPT-SoVITS returned HTTP {response.status_code}: {_error_message(response)}"
            )
        if len(response.content) > MAX_AUDIO_BYTES:
            raise ValueError("GPT-SoVITS audio exceeds size limit")
        logger.info(
            "[GPTSoVITSProvider.synthesize] 合成完成 voice=%s chars=%d 耗时=%.2fs",
            name,
            len(text),
            time.time() - started,
        )
        return await save_wav(self._evidence, response.content, started)


def _error_message(response: httpx.Response) -> str:
    """提取服务端 JSON 错误说明，截断为 200 字以内."""
    try:
        payload = cast(object, response.json())
    except ValueError:
        return response.text[:200]
    if isinstance(payload, dict):
        message = cast(dict[str, object], payload).get("message")
        if isinstance(message, str):
            return message[:200]
    return response.text[:200]
