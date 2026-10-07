# perception/camera/factory.py
"""显式注册相机构造器，装配时选择，不在导入阶段打开设备."""

from collections.abc import Callable

from domain.validation import nonempty_string
from perception.interfaces import CameraSource


class CameraFactory:
    """默认不含任何相机，未知名称明确报错而不回退."""

    def __init__(self) -> None:
        """保存构造器，相机参数由构造器闭包注入."""
        self._builders: dict[str, Callable[[], CameraSource]] = {}

    def register(self, name: str, builder: Callable[[], CameraSource]) -> None:
        """注册相机构造器，不允许覆盖已有名称."""
        name = nonempty_string(name, "camera name")
        if name in self._builders:
            raise ValueError("相机已经注册")
        self._builders[name] = builder

    def create(self, name: str) -> CameraSource:
        """构造未连接的相机，连接和清理由上层生命周期管理."""
        try:
            builder = self._builders[name]
        except KeyError:
            raise ValueError("未知相机来源") from None
        return builder()
