# domain/__init__.py
"""模块之间共享的数据契约与输入校验."""

from domain.models import (
    ActionEvent,
    ActionRecord,
    ActionRequest,
    ActionStatus,
    Pose2D,
    RobotState,
    SkillResult,
    TaskState,
    TaskStatus,
)

__all__ = [
    "ActionEvent",
    "ActionRecord",
    "ActionRequest",
    "ActionStatus",
    "Pose2D",
    "RobotState",
    "SkillResult",
    "TaskState",
    "TaskStatus",
]
