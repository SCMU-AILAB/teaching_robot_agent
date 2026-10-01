# domain/models.py
"""模块间共享的数据契约；动作状态与设备状态分别保存."""

import time
from dataclasses import dataclass, field
from enum import StrEnum


class ActionStatus(StrEnum):
    """动作生命周期状态."""

    QUEUED = "queued"
    RUNNING = "running"
    VERIFYING = "verifying"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        """判断动作是否已进入不可继续执行的终态."""
        return self in {
            self.SUCCEEDED,
            self.FAILED,
            self.CANCELLED,
            self.TIMED_OUT,
        }


class TaskStatus(StrEnum):
    """用户任务生命周期状态."""

    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Pose2D:
    """平面位姿：x、y 单位为米，yaw 单位为弧度."""

    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0


@dataclass(frozen=True)
class RobotState:
    """设备状态快照，更新时间使用 Unix 秒."""

    is_connected: bool
    is_moving: bool
    position: Pose2D
    updated_at: float


@dataclass(frozen=True)
class ActionRequest:
    """动作提交参数，执行期限使用秒."""

    task_id: int
    skill_name: str
    args: dict[str, object] = field(default_factory=dict)
    timeout_s: float = 10.0


@dataclass(frozen=True)
class SkillResult:
    """动作执行结果及支持完成判定的证据."""

    summary: str
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass
class ActionRecord:
    """动作请求、状态、时间与最终结果记录."""

    action_id: int
    raw_request: ActionRequest
    status: ActionStatus = ActionStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    result: SkillResult | None = None
    failure: str | None = None


@dataclass
class TaskState:
    """任务目标、关联动作及业务状态."""

    task_id: int
    user_target: str
    status: TaskStatus = TaskStatus.PENDING
    action_ids: list[int] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    ended_at: float | None = None


@dataclass(frozen=True)
class ActionEvent:
    """动作状态变化通知，发生时间使用 Unix 秒."""

    action_id: int
    task_id: int
    status: ActionStatus
    occurred_at: float = field(default_factory=time.time)
