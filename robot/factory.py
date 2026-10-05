# robot/factory.py
"""显式注册设备构造器，装配时选择，不在导入阶段连接 SDK."""

from collections.abc import Callable

from domain.validation import nonempty_string
from robot.base import RobotAdapter
from robot.simulated import SimulatedAdapter


class RobotAdapterFactory:
    """默认仅有模拟设备，未知硬件名称明确报错而不回退模拟."""

    def __init__(self) -> None:
        """保存构造器；厂商连接参数由构造器闭包注入."""
        self._builders: dict[str, Callable[[], RobotAdapter]] = {
            "simulated": SimulatedAdapter,
        }

    def register(self, name: str, builder: Callable[[], RobotAdapter]) -> None:
        """注册官方适配器的构造器，不允许覆盖已有设备."""
        name = nonempty_string(name, "adapter name")
        if name in self._builders:
            raise ValueError("Adapter already registered")
        self._builders[name] = builder

    def create(self, name: str) -> RobotAdapter:
        """构造未连接设备，连接和清理由 Runtime 生命周期管理."""
        try:
            builder = self._builders[name]
        except KeyError:
            raise ValueError("Unknown robot adapter") from None
        return builder()
