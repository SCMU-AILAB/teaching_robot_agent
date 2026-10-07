# skills/robot_registry.py
"""根据设备实际能力装配可执行的机器人动作."""

from domain.robot import RobotCapability
from robot.base import RobotAdapter
from skills.move_relative import MoveRelativeSkill
from skills.registry import SkillRegistry
from skills.turn_relative import TurnRelativeSkill


def build_robot_skills(robot: RobotAdapter) -> SkillRegistry:
    """没有定位反馈时不开放需要位姿验证的动作，导航暂不注册."""
    registry = SkillRegistry()
    if RobotCapability.LOCALIZATION not in robot.capabilities:
        return registry
    if RobotCapability.MOVE_RELATIVE in robot.capabilities:
        registry.register(MoveRelativeSkill())
    if RobotCapability.TURN_RELATIVE in robot.capabilities:
        registry.register(TurnRelativeSkill())
    return registry
