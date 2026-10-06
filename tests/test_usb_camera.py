# tests/test_usb_camera.py
"""验证通用相机的开启，采集，关闭."""

import unittest

import numpy as np

from perception.camera.usb_camera import UsbCamera, encode_frame
from storage.evidence import EvidenceStore


class EncodeFrameTests(unittest.TestCase):
    """验证像素矩阵能编码成 JPEG 原始帧."""

    def test_encode_frame_returns_jpeg(self) -> None:
        """假图也能验证尺寸与签名，不需要摄像头."""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        raw = encode_frame(frame)

        self.assertEqual(raw.width, 640)
        self.assertEqual(raw.height, 480)
        self.assertEqual(raw.media_type, "image/jpeg")
        self.assertEqual(raw.data[:2], bytes([0xFF, 0xD8]))


class UsbCameraTests(unittest.IsolatedAsyncioTestCase):
    """验证 USB 相机的连接与错误边界."""

    def make_camera(self, _device_index: int = 999) -> UsbCamera:
        """造一台指向不存在设备的相机，不依赖真硬件."""
        return UsbCamera(
            _evidence=EvidenceStore(),
            _camera_id="usb-cam-1",
            _device_index=_device_index,
        )

    async def test_connect_rejects_missing_device(self) -> None:
        """设备打不开时连接失败，不进入采集流程."""
        with self.assertRaises(ValueError):
            await self.make_camera().connect()
