# providers/asr/remote.py
"""上传本地录音到服务器进行识别，本地不加载 ASR 模型."""

import json
from typing import cast

from domain.services import AudioReference, TranscriptResult
from domain.validation import nonempty_string, string_key_dict
from providers.audio.wav import read_wav
from speech.remote import SSHSpeechTransport
from storage.evidence import EvidenceStore


class RemoteASRProvider:
    """沿用 ASR Protocol，证据留在本地，服务器只返回最终文本."""

    def __init__(
        self,
        _store: EvidenceStore,
        _transport: SSHSpeechTransport,
        _language: str = "zh",
    ) -> None:
        """注入传输与存储，不建立连接."""
        self._store: EvidenceStore = _store
        self._transport: SSHSpeechTransport = _transport
        self._language: str = nonempty_string(_language, "language")

    async def transcribe(
        self, audio: AudioReference, timeout_s: float
    ) -> TranscriptResult:
        """识别失败不伪装成空语音，最终空文本仍交由上层正常处理."""
        response = await self._transport.request(
            "asr", await read_wav(self._store, audio), timeout_s, self._language
        )
        value = string_key_dict(cast(object, json.loads(response)), "transcript")
        text = value.get("text")
        language = value.get("language")
        if (
            not isinstance(text, str)
            or not isinstance(language, str)
            or value.get("is_final") is not True
        ):
            raise ValueError("Invalid remote transcript")
        return TranscriptResult(text.strip(), language, True)
