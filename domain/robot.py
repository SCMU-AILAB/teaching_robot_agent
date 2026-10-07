# domain/robot.py
"""与厂商无关的机器人能力及导航目标契约."""

from dataclasses import dataclass
from enum import StrEnum

from domain.models import Pose2D
from domain.validation import finite_float, nonempty_string


class RobotCapability(StrEnum):
    """明确区分距离控制、转向、定位与完整导航."""

    MOVE_RELATIVE = "move_relative"
    TURN_RELATIVE = "turn_relative"
    LOCALIZATION = "localization"
    NAVIGATION = "navigation"


@dataclass(frozen=True)
class NavigationGoal:
    """指定地图坐标系中的目标位姿，米与弧度为统一单位."""

    frame_id: str
    pose: Pose2D

    def __post_init__(self) -> None:
        """拒绝缺失坐标系和非有限位姿."""
        _ = nonempty_string(self.frame_id, "frame_id")
        for name, value in (
            ("x", self.pose.x),
            ("y", self.pose.y),
            ("yaw", self.pose.yaw),
        ):
            _ = finite_float(value, name)


class UnsupportedCapabilityError(RuntimeError):
    """当前设备没有所请求的能力，不允许降级为另一种运动."""
