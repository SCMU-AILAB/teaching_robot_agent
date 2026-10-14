# providers/tts/remote.py
"""服务器生成 WAV，本地登记音频证据，不在服务器播放."""

import time

from domain.services import AudioReference
from domain.validation import nonempty_string
from providers.audio.wav import save_wav
from speech.remote import SSHSpeechTransport
from storage.evidence import EvidenceStore


class RemoteTTSProvider:
    """沿用 TTS Protocol，返回值只代表合成完成."""

    def __init__(
        self,
        _store: EvidenceStore,
        _transport: SSHSpeechTransport,
        _voice: str = "huayan",
    ) -> None:
        """注入服务器传输，音色由环境装配."""
        self._store: EvidenceStore = _store
        self._transport: SSHSpeechTransport = _transport
        self._voice: str = nonempty_string(_voice, "voice")

    async def synthesize(
        self, text: str, voice_id: str | None, timeout_s: float
    ) -> AudioReference:
        """发送文本并校验返回的真实音频，取消不会产生本地迟到证据."""
        text = nonempty_string(text, "text")
        if len(text) > 2000:
            raise ValueError("TTS text exceeds 2000 characters")
        voice = (
            self._voice if voice_id is None else nonempty_string(voice_id, "voice_id")
        )
        started = time.time()
        content = await self._transport.request("tts", text.encode(), timeout_s, voice)
        return await save_wav(self._store, content, started)
