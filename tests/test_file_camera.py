# tests/test_file_camera.py
"""验证文件相机的采集、证据登记与错误边界."""

import struct
import tempfile
import unittest
from pathlib import Path

from perception.camera import FileCamera
from perception.camera.file_camera import read_image_size
from storage.evidence import EvidenceStore

# 1x1 的合法 PNG，与 perception/simulated.py 使用的占位图同源
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000"
    + "b50c0c020000000b4944415478da63fcc300000301010018dd8db0"
    + "0000000049454e44ae426082"
)


def _make_jpeg(_width: int, _height: int, _marker: int = 0xC0) -> bytes:
    """构造最小 JPEG 头部，使尺寸解析测试不依赖图片文件.

    Args:
        _width: 要写入 SOF 段的宽度像素数.
        _height: 要写入 SOF 段的高度像素数.
        _marker: SOF 标记，默认基线 0xC0.

    Returns:
        足以让解析器读到尺寸的 JPEG 字节串.
    """
    return (
        b"\xff\xd8"
        + bytes([0xFF, _marker])
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">H", _height)
        + struct.pack(">H", _width)
        + b"\xff\xd9"
    )


class ReadImageSizeTests(unittest.TestCase):
    """验证从图片字节读取尺寸与格式，不依赖文件系统."""

    def test_png_returns_size_and_type(self) -> None:
        """PNG 按固定偏移返回宽高与媒体类型."""
        self.assertEqual(read_image_size(_PNG), (1, 1, "image/png"))

    def test_jpeg_returns_size_and_type(self) -> None:
        """JPEG 跳段找到 SOF 后返回宽高与媒体类型."""
        self.assertEqual(
            read_image_size(_make_jpeg(1920, 1080)), (1920, 1080, "image/jpeg")
        )

    def test_jpeg_accepts_sof_variants(self) -> None:
        """不同 SOF 标记都应被识别，且宽高偏移一致."""
        for marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xCF):
            with self.subTest(marker=marker):
                self.assertEqual(
                    read_image_size(_make_jpeg(640, 480, marker)),
                    (640, 480, "image/jpeg"),
                )

    def test_jpeg_without_sof_raises(self) -> None:
        """只有普通段而没有 SOF 时抛出明确错误."""
        data = b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00"
        with self.assertRaises(ValueError):
            _ = read_image_size(data)

    def test_unknown_format_raises(self) -> None:
        """既不是 PNG 也不是 JPEG 时拒绝解析."""
        with self.assertRaises(ValueError):
            _ = read_image_size(b"not an image at all")


class FileCameraTests(unittest.IsolatedAsyncioTestCase):
    """验证文件相机的连接、采集、证据登记与关闭边界."""

    def make_camera(
        self, _width: int = 640, _height: int = 480
    ) -> tuple[Path, EvidenceStore, FileCamera]:
        """建临时图片、证据存储与相机，测试结束由框架自动清理目录."""
        tmp = tempfile.TemporaryDirectory()
        _ = self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "frame.jpg"
        _ = path.write_bytes(_make_jpeg(_width, _height))
        store = EvidenceStore()
        camera = FileCamera(
            _evidence=store,
            _camera_id="file-cam-1",
            _image_path=str(path),
        )
        return path, store, camera

    async def test_connect_rejects_missing_file(self) -> None:
        """路径不存在时连接失败，不进入采集流程."""
        _, store, _ = self.make_camera()
        camera = FileCamera(
            _evidence=store,
            _camera_id="missing",
            _image_path="/nonexistent/frame.jpg",
        )
        with self.assertRaises(ValueError):
            await camera.connect()

    async def test_capture_returns_frame_reference(self) -> None:
        """采集返回字段完整、场景版本原样传递的帧引用."""
        _, _, camera = self.make_camera()
        await camera.connect()
        frame = await camera.capture(scene_revision=7)
        self.assertEqual(frame.camera_id, "file-cam-1")
        self.assertEqual(frame.width, 640)
        self.assertEqual(frame.height, 480)
        self.assertEqual(frame.scene_revision, 7)
        self.assertGreater(frame.captured_at, 0)
        self.assertTrue(frame.frame_id)
        self.assertTrue(frame.evidence_id)

    async def test_capture_registers_readable_evidence(self) -> None:
        """登记的证据编号可以读回原始图片字节."""
        path, store, camera = self.make_camera()
        await camera.connect()
        frame = await camera.capture(scene_revision=0)
        self.assertEqual(await store.read(frame.evidence_id), path.read_bytes())

    async def test_frame_id_increases(self) -> None:
        """连续采集时帧序递增，摄像头标识保持不变."""
        _, _, camera = self.make_camera()
        await camera.connect()
        first = await camera.capture(scene_revision=0)
        second = await camera.capture(scene_revision=0)
        self.assertEqual(first.frame_id, "file-cam-1:1")
        self.assertEqual(second.frame_id, "file-cam-1:2")

    async def test_capture_after_close_raises(self) -> None:
        """关闭后拒绝继续采集."""
        _, _, camera = self.make_camera()
        await camera.connect()
        await camera.close()
        with self.assertRaises(RuntimeError):
            _ = await camera.capture(scene_revision=0)

    async def test_close_is_idempotent(self) -> None:
        """重复关闭不报错，符合接口约定."""
        _, _, camera = self.make_camera()
        await camera.close()
        await camera.close()
