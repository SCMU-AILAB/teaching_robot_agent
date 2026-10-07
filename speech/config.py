# speech/config.py
"""真实语音入口的环境配置，不保存部署地址或连接凭据."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from domain.validation import finite_float


@dataclass(frozen=True)
class SpeechConfig:
    """由入口显式加载的真实语音配置."""

    input_device: str
    output_device: str
    sample_rate: int
    max_duration_s: float
    timeout_s: float
    model_path: str
    language: str
    compute_device: str
    compute_type: str
    tts_engine: str
    voice: str

    @classmethod
    def from_env(cls) -> "SpeechConfig":
        """实际检查离线模型和期限后构造，缺配置直接报错而不降级模拟."""
        model = os.environ.get("SPEECH_ASR_MODEL_PATH", "")
        if not model or not (Path(model) / "model.bin").is_file():
            raise ValueError("Set SPEECH_ASR_MODEL_PATH to a downloaded Whisper model")
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
        engine = os.environ.get(
            "SPEECH_TTS_ENGINE", "say" if sys.platform == "darwin" else "espeak-ng"
        )
        voice = os.environ.get(
            "SPEECH_TTS_VOICE", "Tingting" if engine == "say" else "cmn"
        )
        return cls(
            os.environ.get("SPEECH_INPUT_DEVICE", ""),
            os.environ.get("SPEECH_OUTPUT_DEVICE", ""),
            rate,
            duration,
            timeout,
            model,
            os.environ.get("SPEECH_ASR_LANGUAGE", "zh"),
            os.environ.get("SPEECH_ASR_DEVICE", "cpu"),
            os.environ.get("SPEECH_ASR_COMPUTE_TYPE", "int8"),
            engine,
            voice,
        )
