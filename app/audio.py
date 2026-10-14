# app/audio.py
"""语音依赖装配配置，真实适配器可以沿用同一组接口替换模拟器."""

import os
import sys
from dataclasses import dataclass
from typing import Protocol

from domain.services import TranscriptResult
from domain.validation import finite_float
from providers.asr.mock import MockASRProvider
from providers.asr.resident import ASREngine, ResidentASRProvider
from providers.asr.sensevoice_engine import SenseVoiceEngine
from providers.asr.whisper_engine import WhisperEngine
from providers.audio.mock import MockAudioRecorder
from providers.audio.player import MockAudioPlayer
from providers.audio.real_player import PortAudioPlayer
from providers.audio.recorder import PortAudioRecorder
from providers.tts.mock import MockTTSProvider
from providers.tts.system import SystemTTSProvider
from speech.interfaces import ASRProvider, AudioPlayer, AudioRecorder, TTSProvider
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


class AudioLifecycle(Protocol):
    """需要随应用启动加载、随应用关闭释放的语音资源."""

    async def start(self) -> None:
        """加载资源."""
        ...

    async def close(self) -> None:
        """释放资源，重复调用安全."""
        ...


@dataclass(frozen=True)
class AudioAdapters:
    """应用拥有的录音、识别、合成和播放依赖，模拟标记必须显式传入."""

    recorder: AudioRecorder
    asr: ASRProvider
    tts: TTSProvider
    player: AudioPlayer
    simulated: bool
    # 由应用在 start/close 中管理的常驻资源，例如进程内识别模型。
    lifecycle: tuple[AudioLifecycle, ...] = ()
    max_duration_s: float = 15.0
    timeout_s: float = 30.0


def build_simulated_audio(
    evidence: EvidenceStore, transcript: str = "你好"
) -> AudioAdapters:
    """给模拟录音和播放器注入同一个设备租约，不连接实际音频硬件."""
    device = AudioDeviceLease()
    return AudioAdapters(
        MockAudioRecorder(evidence, device),
        MockASRProvider(_result=TranscriptResult(transcript)),
        MockTTSProvider(evidence),
        MockAudioPlayer(evidence, device),
        simulated=True,
    )


def build_asr_engine() -> ASREngine:
    """按环境选择常驻识别引擎：默认中文 SenseVoice，可切换 Whisper.

    Returns:
        尚未加载模型的识别引擎.

    Raises:
        ValueError: 引擎名称未知或模型目录未配置.
    """
    model_path = os.environ.get("SPEECH_ASR_MODEL_PATH", "")
    if not model_path.strip():
        raise ValueError("Set SPEECH_ASR_MODEL_PATH to a downloaded ASR model")
    language = os.environ.get("SPEECH_ASR_LANGUAGE", "zh")
    engine = os.environ.get("SPEECH_ASR_ENGINE", "sensevoice")
    if engine == "sensevoice":
        return SenseVoiceEngine(model_path, language)
    if engine == "whisper":
        return WhisperEngine(
            model_path,
            language,
            os.environ.get("SPEECH_ASR_DEVICE", "cpu"),
            os.environ.get("SPEECH_ASR_COMPUTE_TYPE", "int8"),
        )
    raise ValueError("SPEECH_ASR_ENGINE must be sensevoice or whisper")


def build_real_audio(evidence: EvidenceStore) -> AudioAdapters:
    """按环境装配本机麦克风、常驻识别、系统合成与扬声器，缺配置直接报错.

    Args:
        evidence: 录音与合成音频共用的证据存储.

    Returns:
        非模拟的语音依赖；识别模型在应用启动时加载.

    Raises:
        ValueError: 模型目录、采样率、时长或期限配置非法.
    """
    rate = int(os.environ.get("SPEECH_SAMPLE_RATE", "16000"))
    duration = finite_float(
        float(os.environ.get("SPEECH_MAX_DURATION_S", "15")), "max_duration_s"
    )
    timeout = finite_float(float(os.environ.get("SPEECH_TIMEOUT_S", "30")), "timeout_s")
    if not 8000 <= rate <= 48000 or not 0 < duration <= 60 or not 0 < timeout <= 300:
        raise ValueError("Invalid speech sample rate, duration or timeout")
    engine = os.environ.get(
        "SPEECH_TTS_ENGINE", "say" if sys.platform == "darwin" else "espeak-ng"
    )
    # 与服务器 Piper 的 SPEECH_TTS_VOICE 分开，避免把 huayan 交给系统合成引擎。
    voice = os.environ.get(
        "SPEECH_SYSTEM_TTS_VOICE", "Tingting" if engine == "say" else "cmn"
    )
    device = AudioDeviceLease()
    asr = ResidentASRProvider(evidence, build_asr_engine())
    return AudioAdapters(
        PortAudioRecorder(
            evidence, device, os.environ.get("SPEECH_INPUT_DEVICE", ""), rate
        ),
        asr,
        SystemTTSProvider(evidence, engine, voice),
        PortAudioPlayer(evidence, device, os.environ.get("SPEECH_OUTPUT_DEVICE", "")),
        simulated=False,
        lifecycle=(asr,),
        max_duration_s=duration,
        timeout_s=timeout,
    )
