# speech/config.py
"""真实语音入口的环境配置，不保存部署地址或连接凭据."""

import os
from dataclasses import dataclass

from domain.validation import finite_float
from speech.remote import SpeechServer


@dataclass(frozen=True)
class SpeechConfig:
    """由入口显式加载的真实语音配置."""

    input_device: str
    output_device: str
    sample_rate: int
    max_duration_s: float
    timeout_s: float
    server: SpeechServer
    language: str
    voice: str

    @classmethod
    def from_env(cls) -> "SpeechConfig":
        """本地只检查设备和服务器连接配置，不要求本地模型或 TTS 引擎."""
        server = SpeechServer(
            os.environ.get("SPEECH_SSH_HOST", ""),
            os.environ.get("SPEECH_SSH_USER", ""),
            int(os.environ.get("SPEECH_SSH_PORT", "22")),
            os.environ.get("SPEECH_SERVER_DIRECTORY", ""),
        )
        _ = server.command()
        rate = int(os.environ.get("SPEECH_SAMPLE_RATE", "16000"))
        duration = finite_float(
            float(os.environ.get("SPEECH_MAX_DURATION_S", "15")), "duration"
        )
        timeout = finite_float(
            float(os.environ.get("SPEECH_TIMEOUT_S", "60")), "timeout"
        )
        if (
            not 8000 <= rate <= 48000
            or not 0 < duration <= 60
            or not 0 < timeout <= 300
        ):
            raise ValueError("Invalid speech device rate or timeout")
        return cls(
            os.environ.get("SPEECH_INPUT_DEVICE", ""),
            os.environ.get("SPEECH_OUTPUT_DEVICE", ""),
            rate,
            duration,
            timeout,
            server,
            os.environ.get("SPEECH_ASR_LANGUAGE", "zh"),
            os.environ.get("SPEECH_TTS_VOICE", "huayan"),
        )
