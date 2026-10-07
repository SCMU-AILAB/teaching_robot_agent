# tests/test_camera_lifecycle.py
"""固定设备调用交错顺序，验证取消和关闭不重叠底层作业."""

import asyncio
import threading
import unittest
from typing import override
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from perception.camera.base import BaseCamera, RawFrame
from perception.camera.usb_camera import UsbCamera
from storage.evidence import EvidenceStore


class GatedCamera(BaseCamera):
    """以事件控制连接结束时机."""

    def __init__(self) -> None:
        """初始化控制事件和调用计数."""
        super().__init__(EvidenceStore(), "gate")
        self.entered: asyncio.Event = asyncio.Event()
        self.allow: asyncio.Event = asyncio.Event()
        self.opens: int = 0
        self.releases: int = 0

    @override
    async def open_device(self) -> None:
        """记录打开并等待测试放行."""
        self.opens += 1
        self.entered.set()
        _ = await self.allow.wait()

    @override
    async def grab_frame(self) -> RawFrame:
        """返回测试字节，生命周期测试不解析图片."""
        return RawFrame(b"test", 1, 1, "image/png")

    @override
    async def release_device(self) -> None:
        """记录已释放设备."""
        self.releases += 1


class ThreadCapture:
    """模拟不能由 asyncio 取消的阻塞相机驱动."""

    def __init__(self) -> None:
        """初始化线程同步事件及释放检查."""
        self.entered: threading.Event = threading.Event()
        self.allow: threading.Event = threading.Event()
        self.finished: threading.Event = threading.Event()
        self.overlap: bool = False
        self.releases: int = 0

    def read(self) -> tuple[bool, cv2.typing.MatLike]:
        """等待线程放行再交回有效像素."""
        self.entered.set()
        _ = self.allow.wait(3)
        self.finished.set()
        return True, np.zeros((1, 1, 3), dtype=np.uint8)

    def release(self) -> None:
        """检查释放是否与读取重叠."""
        self.overlap = self.entered.is_set() and not self.finished.is_set()
        self.releases += 1


class CameraLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """验证原审查复现的相机资源竞争."""

    async def test_concurrent_connect_opens_once(self) -> None:
        """并发连接在锁内复查，只打开一次."""
        camera = GatedCamera()
        first = asyncio.create_task(camera.connect())
        second = asyncio.create_task(camera.connect())
        async with asyncio.timeout(2):
            _ = await camera.entered.wait()
            camera.allow.set()
            _ = await asyncio.gather(first, second)
            await camera.close()
        self.assertEqual(camera.opens, 1)
        self.assertEqual(camera.releases, 1)

    async def test_close_during_connect_waits_and_releases(self) -> None:
        """连接未完成时关闭不得提前返回，关闭完成后不能采集."""
        camera = GatedCamera()
        starting = asyncio.create_task(camera.connect())
        async with asyncio.timeout(2):
            _ = await camera.entered.wait()
            closing = asyncio.create_task(camera.close())
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            camera.allow.set()
            _ = await asyncio.gather(starting, closing)
        self.assertEqual(camera.releases, 1)
        with self.assertRaises(RuntimeError):
            _ = await camera.capture(0)

    async def test_cancelled_connect_releases_late_device(self) -> None:
        """取消连接后仍等待打开完成，再回收迟到句柄."""
        camera = GatedCamera()
        starting = asyncio.create_task(camera.connect())
        async with asyncio.timeout(2):
            _ = await camera.entered.wait()
            _ = starting.cancel()
            await asyncio.sleep(0)
            self.assertFalse(starting.done())
            camera.allow.set()
            with self.assertRaises(asyncio.CancelledError):
                await starting
        self.assertEqual(camera.releases, 1)
        with self.assertRaises(RuntimeError):
            _ = await camera.capture(0)

    async def test_cancelled_capture_keeps_lock_until_thread_finishes(self) -> None:
        """重复取消不能提前释放采集锁或与驱动读取并发关闭."""
        device = ThreadCapture()
        camera = UsbCamera(EvidenceStore(), "usb")
        cap = MagicMock(spec=cv2.VideoCapture)
        cap.configure_mock(
            **{
                "isOpened.return_value": True,
                "read.side_effect": device.read,
                "release.side_effect": device.release,
            }
        )
        with patch("perception.camera.usb_camera.cv2.VideoCapture", return_value=cap):
            await camera.connect()
            capture = asyncio.create_task(camera.capture(0))
            closing: asyncio.Task[None] | None = None
            try:
                async with asyncio.timeout(2):
                    self.assertTrue(await asyncio.to_thread(device.entered.wait, 1))
                    _ = capture.cancel()
                    await asyncio.sleep(0)
                    _ = capture.cancel()
                    closing = asyncio.create_task(camera.close())
                    await asyncio.sleep(0)
                    self.assertFalse(capture.done())
                    self.assertFalse(closing.done())
                    self.assertEqual(device.releases, 0)
                    device.allow.set()
                    with self.assertRaises(asyncio.CancelledError):
                        _ = await capture
                    await closing
                self.assertFalse(device.overlap)
                self.assertEqual(device.releases, 1)
            finally:
                device.allow.set()
                _ = await asyncio.gather(capture, return_exceptions=True)
                if closing is not None:
                    _ = await asyncio.gather(closing, return_exceptions=True)
                await camera.close()
