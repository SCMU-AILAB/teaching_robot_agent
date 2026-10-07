# providers/audio/wav.py
"""真实语音适配器共享的有界 PCM WAV 校验与证据登记."""

import io
import time
import wave
from dataclasses import dataclass
from uuid import uuid4

from domain.services import AudioReference
from domain.validation import finite_float
from storage.evidence import EvidenceStore


@dataclass(frozen=True)
class PcmAudio:
    """已校验的十六位 PCM 数据，仅供适配器内部使用."""

    frames: bytes
    sample_rate: int
    channels: int

    @property
    def duration(self) -> float:
        """按实际帧数计算音频时长."""
        return len(self.frames) / (2 * self.channels * self.sample_rate)


def decode_wav(content: bytes) -> PcmAudio:
    """拒绝空、损坏、过大或非十六位 PCM 音频."""
    if not content or len(content) > 32 * 1024 * 1024:
        raise ValueError("Empty or oversized WAV")
    try:
        with wave.open(io.BytesIO(content), "rb") as wav:
            channels = wav.getnchannels()
            rate = wav.getframerate()
            count = wav.getnframes()
            if (
                wav.getsampwidth() != 2
                or channels not in (1, 2)
                or not 8000 <= rate <= 192000
                or not 0 < count <= rate * 120
            ):
                raise ValueError("Unsupported or empty PCM WAV")
            frames = wav.readframes(count)
            if len(frames) != count * channels * 2:
                raise ValueError("Truncated WAV")
    except (wave.Error, EOFError) as error:
        raise ValueError("Invalid WAV") from error
    return PcmAudio(frames, rate, channels)


def encode_wav(audio: PcmAudio) -> bytes:
    """将十六位 PCM 帧编码为可读取的 WAV."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(audio.channels)
        wav.setsampwidth(2)
        wav.setframerate(audio.sample_rate)
        wav.writeframes(audio.frames)
    return buffer.getvalue()


async def save_wav(
    store: EvidenceStore, content: bytes, started_at: float
) -> AudioReference:
    """实际校验后保存音频，不向上层暴露文件路径."""
    audio = decode_wav(content)
    reference = await store.save(content, "audio/wav", started_at)
    return AudioReference(
        uuid4().hex,
        reference.evidence_id,
        started_at,
        time.time(),
        audio.sample_rate,
        audio.channels,
        audio.duration,
    )


async def read_wav(store: EvidenceStore, reference: AudioReference) -> bytes:
    """核对引用元数据与登记内容，防止格式不符的音频进入设备."""
    if store.get(reference.evidence_id).media_type != "audio/wav":
        raise ValueError("Evidence is not WAV")
    content = await store.read(reference.evidence_id)
    audio = decode_wav(content)
    duration = finite_float(reference.duration_s, "duration_s")
    if (
        audio.sample_rate != reference.sample_rate_hz
        or audio.channels != reference.channels
        or abs(audio.duration - duration) > 1 / audio.sample_rate
    ):
        raise ValueError("Audio reference does not match WAV")
    return content
