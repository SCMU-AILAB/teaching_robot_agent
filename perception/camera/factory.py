# perception/camera/factory.py
"""按配置调用对应摄像头."""

from perception.camera.file_camera import FileCamera
from perception.interfaces import CameraSource
from storage.evidence import EvidenceStore


def create_camera(
    _camera_type: str,
    _evidence: EvidenceStore,
    _camera_id: str,
    _source: str,
) -> CameraSource:
    """按相机类型创建对应的相机实现.

    Args:
        _camera_type: 相机类型名，当前支持 "file".
        _evidence: 证据存储，由核心注入.
        _camera_id: 相机标识，写入每次采集的帧引用.
        _source: 图片来源；"file" 类型下为图片路径.

    Returns:
        满足 CameraSource 协议、可 connect / capture / close 的相机实例.

    Raises:
        ValueError: 相机类型尚未实现或不被支持时抛出.
    """
    if _camera_type == "file":
        return FileCamera(_evidence, _camera_id, _source)
    raise ValueError(f"不支持的相机类型：{_camera_type}")
