# tests/test_file_camera.py
"""验证文件相机的采集、证据登记与错误边界."""

import io
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from perception.camera import FileCamera
from perception.camera.file_camera import read_image_size
from storage.evidence import EvidenceStore


def _make_image(
    width: int, height: int, format_name: str, *, progressive: bool = False
) -> bytes:
    """用真实编码器生成可解码图片，避免伪造头部掩盖损坏."""
    stream = io.BytesIO()
    with Image.new("RGB", (width, height), "white") as image:
        image.save(stream, format=format_name, progressive=progressive)
    return stream.getvalue()


_PNG = _make_image(1, 1, "PNG")


def _make_jpeg(_width: int, _height: int, _marker: int = 0xC0) -> bytes:
    """生成基线或渐进 JPEG 的完整字节."""
    return _make_image(_width, _height, "JPEG", progressive=_marker == 0xC2)


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
        for marker in (0xC0, 0xC2):
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

    def test_truncated_png_raises(self) -> None:
        """只有签名的 PNG 说明数据被截断，不能返回 0 乘 0 当成功."""
        with self.assertRaises(ValueError):
            _ = read_image_size(b"\x89PNG")

    def test_truncated_jpeg_raises(self) -> None:
        """SOF 段没写全的 JPEG 同样拒绝解析."""
        with self.assertRaises(ValueError):
            _ = read_image_size(b"\xff\xd8\xff\xc0\x08\x00\x01")

    def test_valid_header_with_corrupt_payload_is_rejected(self) -> None:
        """拒绝头部伪造、CRC 损坏及缺失像素内容的图片."""
        corrupt = bytearray(_PNG)
        corrupt[-20] ^= 1
        for content in (
            b"\x89PNG" + b"\x00" * 12 + (1).to_bytes(4, "big") * 2,
            bytes(corrupt),
            _PNG[:24],
            _make_jpeg(8, 8)[:-30],
        ):
            with self.subTest(size=len(content)), self.assertRaises(ValueError):
                _ = read_image_size(content)

    def test_zero_size_raises(self) -> None:
        """宽高为 0 的 PNG 头部属于非法尺寸."""
        header = _PNG[:16] + b"\x00" * 8 + _PNG[24:]
        with self.assertRaises(ValueError):
            _ = read_image_size(header)


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

    async def test_reconnect_allows_capture_again(self) -> None:
        """关闭后重新连接应该恢复可用，而不是继续报 camera is closed."""
        _, _, camera = self.make_camera()
        await camera.connect()
        await camera.close()
        await camera.connect()
        frame = await camera.capture(scene_revision=0)
        self.assertTrue(frame.frame_id)
