# robot/__init__.py
"""机器人设备接口与模拟实现."""

from robot.base import RobotAdapter
from robot.simulated import SimulatedAdapter, SimulationMode

__all__ = ["RobotAdapter", "SimulatedAdapter", "SimulationMode"]
