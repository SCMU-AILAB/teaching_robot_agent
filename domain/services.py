# domain/services.py
"""感知、媒体和语音模块共享的数据契约."""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class EvidenceReference:
    """由核心存储分配的媒体引用，不暴露磁盘路径."""

    evidence_id: str
    media_type: str
    created_at: float
    captured_at: float
    size_bytes: int


@dataclass(frozen=True)
class FrameReference:
    """相机实际采集的图像及场景版本."""

    frame_id: str
    camera_id: str
    captured_at: float
    width: int
    height: int
    evidence_id: str
    scene_revision: int


@dataclass(frozen=True)
class ObjectObservation:
    """本次观察中的对象，边界框为归一化坐标."""

    object_id: str
    label: str
    attributes: tuple[tuple[str, str], ...] = ()
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class RelationObservation:
    """同一次观察中两个对象的关系."""

    subject_id: str
    relation: str
    object_id: str


@dataclass(frozen=True)
class VisionRequest:
    """视觉推理输入，模型适配器按引用读取图片."""

    request_id: str
    question: str
    frame: FrameReference
    timeout_s: float


@dataclass(frozen=True)
class VisionAnalysis:
    """经适配器校验的模型输出，不包含执行命令."""

    summary: str
    model_id: str
    simulated: bool
    objects: tuple[ObjectObservation, ...] = ()
    relations: tuple[RelationObservation, ...] = ()


@dataclass(frozen=True)
class ObservationRequest:
    """核心提交的一次场景观察请求."""

    observation_id: str
    task_id: int
    question: str
    max_age_s: float = 2.0
    timeout_s: float = 30.0


@dataclass(frozen=True)
class ObservationResult:
    """观察结果及真实帧来源，内部不可变以隔离消费者."""

    observation_id: str
    task_id: int
    question: str
    frame: FrameReference
    analysis: VisionAnalysis
    analyzed_at: float
    from_cache: bool
    stale: bool


@dataclass(frozen=True)
class AudioReference:
    """已登记音频的格式和起止时间."""

    audio_id: str
    evidence_id: str
    started_at: float
    ended_at: float
    sample_rate_hz: int
    channels: int
    duration_s: float


@dataclass(frozen=True)
class TranscriptResult:
    """语音识别结果，无语音使用空文本."""

    text: str
    language: str | None = None
    is_final: bool = True


class PlaybackStatus(StrEnum):
    """播放完成与中途停止分别记录."""

    PLAYING = "playing"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class PlaybackState:
    """播放器状态，只有实际结束后才填写结束时间."""

    playback_id: str
    status: PlaybackStatus
    started_at: float
    ended_at: float | None = None
    error: str | None = None


class ServiceError(Exception):
    """服务错误携带可映射到接口响应的错误码."""

    def __init__(self, _code: int, _message: str, _retryable: bool = False) -> None:
        """保存服务错误分类，不吞掉底层异常链."""
        super().__init__(_message)
        self.code: int = _code
        self.message: str = _message
        self.retryable: bool = _retryable
