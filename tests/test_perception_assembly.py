# tests/test_perception_assembly.py
"""主控制台感知装配：模拟与真实相机的选择及配置校验."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.perception import build_perception
from perception.camera import FileCamera, UsbCamera
from perception.perception_service import LivePerception
from perception.simulated import SimulatedPerception
from storage.evidence import EvidenceStore

LIVE_ENV = {
    "ROBOT_PERCEPTION": "live",
    "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
}


class PerceptionAssemblyTest(unittest.TestCase):
    """build_perception 的环境选择."""

    def test_default_is_simulated_without_camera(self) -> None:
        """未设置时使用模拟感知，不需要相机."""
        with patch.dict(os.environ, {}, clear=False):
            _ = os.environ.pop("ROBOT_PERCEPTION", None)
            adapters = build_perception(EvidenceStore())
        self.assertIsInstance(adapters.service, SimulatedPerception)
        self.assertIsNone(adapters.camera)
        self.assertTrue(adapters.simulated)

    def test_unknown_mode_is_rejected(self) -> None:
        """未知模式直接报错，不退回模拟."""
        with (
            patch.dict(os.environ, {"ROBOT_PERCEPTION": "camera"}),
            self.assertRaisesRegex(ValueError, "ROBOT_PERCEPTION"),
        ):
            _ = build_perception(EvidenceStore())

    def test_live_file_camera_builds_live_perception(self) -> None:
        """File 相机装配为真实感知，构造时不连接相机."""
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "photo.png"
            _ = image.write_bytes(b"placeholder")
            env = {
                **LIVE_ENV,
                "PERCEPTION_CAMERA": "file",
                "PERCEPTION_IMAGE": str(image),
            }
            with patch.dict(os.environ, env):
                adapters = build_perception(EvidenceStore())
        self.assertIsInstance(adapters.service, LivePerception)
        self.assertIsInstance(adapters.camera, FileCamera)
        self.assertFalse(adapters.simulated)

    def test_live_usb_camera_and_invalid_settings(self) -> None:
        """Usb 相机解析设备号；未知相机、非法设备号、缺少图片路径均报错."""
        with patch.dict(
            os.environ,
            {**LIVE_ENV, "PERCEPTION_CAMERA": "usb", "PERCEPTION_DEVICE": "1"},
        ):
            self.assertIsInstance(build_perception(EvidenceStore()).camera, UsbCamera)
        cases = [
            {"PERCEPTION_CAMERA": "csi"},
            {"PERCEPTION_CAMERA": "usb", "PERCEPTION_DEVICE": "front"},
            {"PERCEPTION_CAMERA": "file", "PERCEPTION_IMAGE": ""},
        ]
        for case in cases:
            with (
                self.subTest(case=case),
                patch.dict(os.environ, {**LIVE_ENV, **case}),
                self.assertRaises(ValueError),
            ):
                _ = build_perception(EvidenceStore())


if __name__ == "__main__":
    _ = unittest.main()
