# api/schemas.py
"""请求校验模型与响应结构序列化，字段与 API_CONTRACT.md 第 2、3 节一一对应."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.errors import ErrorCode
from domain.models import ActionRecord, TaskState
from domain.services import ObservationResult

type JsonObject = dict[str, object]

DEFAULT_USER_TARGET = "语音或文字对话"
_POSITIONS = frozenset({"左侧", "中间", "右侧", "不确定"})

RequestId = Annotated[str, Field(min_length=1, max_length=128)]


class _Request(BaseModel):
    """所有请求体：严格类型，出现未定义字段即拒绝."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )

    @field_validator("*")
    @classmethod
    def _reject_blank(cls, value: object) -> object:
        """字符串字段不接受只有空白的内容."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("must not be blank")
        return value


class CreateTaskRequest(_Request):
    """POST /tasks."""

    client_request_id: RequestId
    user_target: Annotated[str, Field(min_length=1, max_length=2000)] | None = None


class UpdateTaskRequest(_Request):
    """PATCH /tasks/{task_id}."""

    status: Literal["cancelling", "completed"]


class CreateMessageRequest(_Request):
    """POST /tasks/{task_id}/messages."""

    client_request_id: RequestId
    text: Annotated[str, Field(min_length=1, max_length=2000)]


class CreateRecordingRequest(_Request):
    """POST /tasks/{task_id}/recordings."""

    client_request_id: RequestId
    max_duration_s: Annotated[float, Field(ge=1, le=60, allow_inf_nan=False)] = 15.0


class UpdateRecordingRequest(_Request):
    """PATCH /tasks/{task_id}/recordings/{recording_id}."""

    status: Literal["transcribing", "cancelled"]


def validation_message(errors: Sequence[Mapping[str, object]]) -> str:
    """把框架校验错误转换为 40001 的中文说明，只取第一处错误."""
    if not errors:
        return "请求不合法"
    first = errors[0]
    location = first.get("loc")
    items = (
        cast(Sequence[object], location) if isinstance(location, tuple | list) else ()
    )
    parts = [str(item) for item in items]
    # 去掉 body/path/query 前缀，只保留字段名。
    field = ".".join(parts[1:] if len(parts) > 1 else parts) or "请求体"
    return f"字段 {field} 不合法：{first.get('msg', '')}"


def dump_json(value: object) -> str:
    """紧凑 UTF-8 JSON，拒绝 NaN 与 Infinity."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def resource_error(code: ErrorCode, message: str) -> JsonObject:
    """ResourceError."""
    return {"code": int(code), "message": message}


def task_payload(task: TaskState) -> JsonObject:
    """TaskResponse."""
    return {
        "task_id": task.task_id,
        "status": task.status.value,
        "user_target": task.user_target,
        "created_at": task.created_at,
        "ended_at": task.ended_at,
    }


def action_payload(record: ActionRecord) -> JsonObject:
    """ActionResponse；args 是模型或宿主提交的 JSON 参数副本."""
    return {
        "action_id": record.action_id,
        "skill_name": record.raw_request.skill_name,
        "args": json.loads(dump_json(record.raw_request.args)),
        "status": record.status.value,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "summary": record.result.summary if record.result is not None else None,
        "failure": record.failure,
    }


def observation_payload(
    observation: ObservationResult, *, frame_available: bool
) -> JsonObject:
    """ObservationResponse；物体方位来自视觉适配器的 position 属性."""
    objects: list[JsonObject] = []
    for item in observation.analysis.objects:
        position = dict(item.attributes).get("position", "不确定")
        objects.append(
            {
                "label": item.label,
                "position": position if position in _POSITIONS else "不确定",
            }
        )
    return {
        "observation_id": observation.observation_id,
        "task_id": observation.task_id,
        "question": observation.question,
        "summary": observation.analysis.summary,
        "objects": objects,
        "captured_at": observation.frame.captured_at,
        "analyzed_at": observation.analyzed_at,
        "stale": observation.stale,
        "simulated": observation.analysis.simulated,
        "frame_evidence_id": (
            observation.frame.evidence_id if frame_available else None
        ),
    }


@dataclass
class MessageRecord:
    """MessageResponse 的服务端记录；只在事件循环内修改."""

    message_id: str
    task_id: int
    role: Literal["user", "assistant"]
    text: str
    source: Literal["text", "speech", "agent"]
    status: Literal[
        "accepted", "processing", "answered", "rejected", "cancelled", "final"
    ]
    created_at: float
    reply_to: str | None = None
    speech_action_id: int | None = None
    error: JsonObject | None = None

    @property
    def pending(self) -> bool:
        """用户消息尚未处理完."""
        return self.status in {"accepted", "processing"}

    def payload(self) -> JsonObject:
        """MessageResponse."""
        return {
            "message_id": self.message_id,
            "task_id": self.task_id,
            "role": self.role,
            "text": self.text,
            "source": self.source,
            "status": self.status,
            "reply_to": self.reply_to,
            "speech_action_id": self.speech_action_id,
            "error": self.error,
            "created_at": self.created_at,
        }


@dataclass
class RecordingRecord:
    """RecordingResponse 的服务端记录；只在事件循环内修改."""

    recording_id: str
    task_id: int
    started_at: float
    status: Literal["recording", "transcribing", "completed", "failed", "cancelled"] = (
        "recording"
    )
    ended_at: float | None = None
    transcript: str | None = None
    message_id: str | None = None
    error: JsonObject | None = None

    @property
    def active(self) -> bool:
        """录音或识别尚未结束."""
        return self.status in {"recording", "transcribing"}

    def payload(self) -> JsonObject:
        """RecordingResponse."""
        return {
            "recording_id": self.recording_id,
            "task_id": self.task_id,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "transcript": self.transcript,
            "message_id": self.message_id,
            "error": self.error,
        }
