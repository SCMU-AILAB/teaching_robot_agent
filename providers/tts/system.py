# providers/tts/system.py
"""调用系统离线语音引擎生成真实人声音频，合成不会直接播放."""

import asyncio
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from domain.services import AudioReference
from domain.validation import finite_float, nonempty_string
from providers.audio.wav import save_wav
from speech._process import run_process
from storage.evidence import EvidenceStore


class SystemTTSProvider:
    """macOS 使用 say，Linux 使用 eSpeak NG；均不上传文本或访问云端."""

    def __init__(
        self,
        _evidence: EvidenceStore,
        _engine: str,
        _voice: str,
        _executable: str | None = None,
    ) -> None:
        """显式选择系统引擎及音色，不在构造时运行合成."""
        if _engine not in {"say", "espeak-ng"}:
            raise ValueError("TTS engine must be say or espeak-ng")
        self._evidence: EvidenceStore = _evidence
        self._engine: str = _engine
        self._voice: str = nonempty_string(_voice, "voice")
        self._executable: str = _executable or _engine

    async def synthesize(
        self, text: str, voice_id: str | None, timeout_s: float
    ) -> AudioReference:
        """以标准输入传入文本，进程退出后校验并登记 WAV，取消时删除临时文件."""
        text = nonempty_string(text, "text")
        if len(text) > 2000:
            raise ValueError("TTS text exceeds 2000 characters")
        voice = nonempty_string(
            self._voice if voice_id is None else voice_id, "voice_id"
        )
        timeout = finite_float(timeout_s, "timeout_s")
        if timeout <= 0:
            raise ValueError("TTS timeout must be positive")
        started_at = time.time()
        async with asyncio.timeout(timeout):
            with TemporaryDirectory(prefix="speech-tts-") as directory:
                path = Path(directory) / "speech.wav"
                if self._engine == "say":
                    command = (
                        self._executable,
                        "-v",
                        voice,
                        "-o",
                        str(path),
                        "--file-format=WAVE",
                        "--data-format=LEI16@16000",
                        "-f",
                        "-",
                    )
                else:
                    command = (
                        self._executable,
                        "-v",
                        voice,
                        "-w",
                        str(path),
                        "--stdin",
                    )
                _ = await run_process(command, text.encode("utf-8"), timeout)
                if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
                    raise ValueError("TTS did not produce a valid audio file")
                return await save_wav(self._evidence, path.read_bytes(), started_at)
