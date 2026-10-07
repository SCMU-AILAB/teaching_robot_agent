# tests/test_camera_factory.py
"""验证相机构造器的注册、选择与错误边界."""

import unittest

from perception.camera.factory import CameraFactory
from perception.camera.file_camera import FileCamera
from storage.evidence import EvidenceStore


def make_file_camera() -> FileCamera:
    """造一台指向临时路径的文件相机，构造阶段不会打开文件."""
    return FileCamera(
        _evidence=EvidenceStore(), _camera_id="file-1", _image_path="/tmp/frame.jpg"
    )


class CameraFactoryTests(unittest.TestCase):
    """验证相机构造器的注册、选择与错误边界."""

    def test_create_returns_registered_camera(self) -> None:
        """注册过的名字能造出对应实现，且构造不等于连接."""
        factory = CameraFactory()
        factory.register("file", make_file_camera)
        self.assertIsInstance(factory.create("file"), FileCamera)

    def test_unknown_name_raises(self) -> None:
        """未知名字明确报错，不回退到任何默认相机."""
        with self.assertRaises(ValueError):
            _ = CameraFactory().create("nope")

    def test_register_rejects_duplicate(self) -> None:
        """已有名字不允许覆盖."""
        factory = CameraFactory()
        factory.register("file", make_file_camera)
        with self.assertRaises(ValueError):
            factory.register("file", make_file_camera)
