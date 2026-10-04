# perception/camera/__init__.py
"""相机采集实现."""

from perception.camera.factory import create_camera
from perception.camera.file_camera import FileCamera

__all__ = ["FileCamera", "create_camera"]
