# skills/__init__.py
"""机器人动作定义与注册模块."""

from skills.base import RobotSkill
from skills.move_relative import MoveRelativeSkill
from skills.registry import SkillRegistry

__all__ = ["RobotSkill", "MoveRelativeSkill", "SkillRegistry"]
