# app/depth.py
"""按环境变量装配深度避障守护与探索设置；模块导入时不打开设备."""

import math
import os

from agent.exploration import ExplorationConfig
from perception.depth.camera import DepthSensor
from perception.depth.geometry import DepthGeometry
from robot.base import RobotAdapter
from robot.obstacle_guard import GuardedRobotAdapter


def _float_env(name: str, default: str | None = None) -> float:
    """读取数值环境变量；缺少且无默认值时报错.

    Raises:
        ValueError: 缺少必填项或不是有限数值。
    """
    raw = os.environ.get(name, default)
    if raw is None or not raw.strip():
        raise ValueError(f"{name} is required when DEPTH_CAMERA=realsense")
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def depth_enabled() -> bool:
    """DEPTH_CAMERA 为 realsense 时启用.

    Raises:
        ValueError: 取值未知。
    """
    mode = os.environ.get("DEPTH_CAMERA", "off")
    if mode not in {"off", "realsense"}:
        raise ValueError("DEPTH_CAMERA must be off or realsense")
    return mode == "realsense"


def depth_geometry_from_env() -> DepthGeometry:
    """读取摄像头安装与车身尺寸."""
    return DepthGeometry(
        camera_height_m=_float_env("DEPTH_CAMERA_HEIGHT_M"),
        camera_pitch_rad=math.radians(_float_env("DEPTH_CAMERA_PITCH_DEG")),
        body_height_m=_float_env("ROBOT_BODY_HEIGHT_M", "0.3"),
        body_width_m=_float_env("ROBOT_BODY_WIDTH_M", "0.25"),
    )


def guard_from_env(robot: RobotAdapter) -> RobotAdapter:
    """启用深度时给底盘包上避障守护，否则原样返回."""
    if not depth_enabled():
        return robot
    sensor = DepthSensor(
        depth_geometry_from_env(), os.environ.get("DEPTH_DEVICE") or None
    )
    return GuardedRobotAdapter(
        robot, sensor, _stop_distance_m=_float_env("OBSTACLE_STOP_M", "0.4")
    )


def exploration_from_env() -> ExplorationConfig | None:
    """启用深度时返回探索设置."""
    if not depth_enabled():
        return None
    return ExplorationConfig(
        radius_m=_float_env("EXPLORE_RADIUS_M", "3"),
        max_duration_s=_float_env("EXPLORE_MAX_S", "300"),
        stop_distance_m=_float_env("OBSTACLE_STOP_M", "0.4"),
    )
