# providers/tts/mock.py
"""生成确定性测试音调，不连接网络、不生成真实人声."""

import asyncio
import io
import math
import struct
import time
import wave
from uuid import uuid4

from domain.services import AudioReference
from domain.validation import finite_float, nonempty_string
from storage.evidence import EvidenceStore


class MockTTSProvider:
    """以可解析 PCM WAV 验证合成链路，支持注入失败和延迟."""

    def __init__(
        self,
        _evidence: EvidenceStore,
        _duration_s: float = 0.1,
        _delay_s: float = 0.0,
        _error: Exception | None = None,
        _release: asyncio.Event | None = None,
    ) -> None:
        """注入存储、测试片段时长和模拟响应行为."""
        self._evidence: EvidenceStore = _evidence
        self._duration_s: float = finite_float(_duration_s, "duration_s")
        self._delay_s: float = finite_float(_delay_s, "delay_s")
        if not 1 / 16000 <= self._duration_s <= 60 or self._delay_s < 0:
            raise ValueError("Invalid mock audio duration or delay")
        self._error: Exception | None = _error
        self._release: asyncio.Event | None = _release
        self.entered: asyncio.Event = asyncio.Event()
        self.call_count: int = 0
        self.last_voice_id: str | None = None

    async def synthesize(
        self, text: str, voice_id: str | None, timeout_s: float
    ) -> AudioReference:
        """生成测试音调引用，完成不意味着已播放.

        Args:
            text: 非空文本，模拟器不进行真实语音合成。
            voice_id: 记录所选音色，仅用于验证参数传递。
            timeout_s: 合成期限，单位为秒。

        Returns:
            与实际 WAV 内容匹配的音频引用。

        Raises:
            ValueError: 参数非法或证据存储容量不足。
            TimeoutError: 合成超过期限。
            Exception: 测试注入的原始合成失败。
        """
        # ========== Step1: 校验参数并等待模拟响应 ==========
        _ = nonempty_string(text, "text")
        if voice_id is not None:
            _ = nonempty_string(voice_id, "voice_id")
        timeout = finite_float(timeout_s, "timeout_s")
        if timeout <= 0:
            raise ValueError("timeout_s must be positive")
        self.last_voice_id = voice_id
        self.call_count += 1
        self.entered.set()
        async with asyncio.timeout(timeout):
            if self._release is not None:
                _ = await self._release.wait()
            await asyncio.sleep(self._delay_s)
            if self._error is not None:
                raise self._error
            # ========== Step2: 写入实际可读取的测试音频 ==========
            frame_count = round(self._duration_s * 16000)
            content = b"".join(
                struct.pack("<h", round(2000 * math.sin(2 * math.pi * 440 * i / 16000)))
                for i in range(frame_count)
            )
            stream = io.BytesIO()
            with wave.open(stream, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(content)
            captured_at = time.time()
            reference = await self._evidence.save(
                stream.getvalue(), "audio/wav", captured_at
            )
            duration = frame_count / 16000
            return AudioReference(
                uuid4().hex,
                reference.evidence_id,
                captured_at,
                captured_at + duration,
                16000,
                1,
                duration,
            )
