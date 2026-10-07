# providers/tts/piper.py
"""服务器离线 Piper 神经语音合成，输出 WAV，不访问任何音频设备."""

import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from domain.services import AudioReference
from domain.validation import finite_float, nonempty_string
from providers.audio.wav import save_wav
from speech._process import run_process
from storage.evidence import EvidenceStore


class PiperTTSProvider:
    """一个实例对应一个已部署音色，取消和超时会回收合成子进程."""

    def __init__(
        self, _store: EvidenceStore, _model_path: str, _voice: str = "huayan"
    ) -> None:
        """注入服务器模型路径和音色标识，构造不加载模型."""
        self._store: EvidenceStore = _store
        self._model: str = nonempty_string(_model_path, "model_path")
        self._voice: str = nonempty_string(_voice, "voice")

    async def synthesize(
        self, text: str, voice_id: str | None, timeout_s: float
    ) -> AudioReference:
        """在有界子进程中生成中文音频，完成后才登记本轮证据."""
        text = nonempty_string(text, "text")
        if len(text) > 2000:
            raise ValueError("TTS text exceeds 2000 characters")
        if voice_id is not None and voice_id != self._voice:
            raise ValueError("Requested TTS voice is not deployed")
        timeout = finite_float(timeout_s, "timeout_s")
        if timeout <= 0:
            raise ValueError("TTS timeout must be positive")
        if not Path(self._model).is_file() or not Path(self._model + ".json").is_file():
            raise ValueError("Piper model or configuration is missing")
        started = time.time()
        with TemporaryDirectory(prefix="speech-piper-") as directory:
            path = Path(directory) / "speech.wav"
            command = (
                sys.executable,
                "-m",
                "piper",
                "-m",
                self._model,
                "-f",
                str(path),
            )
            _ = await run_process(command, text.encode(), timeout)
            if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("Piper did not produce a valid WAV")
            return await save_wav(self._store, path.read_bytes(), started)
