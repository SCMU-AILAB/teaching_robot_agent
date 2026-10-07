# app/audio.py
"""语音依赖装配配置，真实适配器可以沿用同一组接口替换模拟器."""

from dataclasses import dataclass

from domain.services import TranscriptResult
from providers.asr.mock import MockASRProvider
from providers.audio.mock import MockAudioRecorder
from providers.audio.player import MockAudioPlayer
from providers.tts.mock import MockTTSProvider
from speech.interfaces import ASRProvider, AudioPlayer, AudioRecorder, TTSProvider
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


@dataclass(frozen=True)
class AudioAdapters:
    """应用拥有的录音、识别、合成和播放依赖，模拟标记必须显式传入."""

    recorder: AudioRecorder
    asr: ASRProvider
    tts: TTSProvider
    player: AudioPlayer
    simulated: bool


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
