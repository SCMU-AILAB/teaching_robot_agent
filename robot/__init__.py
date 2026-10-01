# robot/__init__.py
from robot.base import RobotAdapter
from robot.simulated import SimulatedAdapter, SimulationMode

__all__ = ["RobotAdapter", "SimulatedAdapter", "SimulationMode"]
