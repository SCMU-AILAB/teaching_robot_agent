# tests/test_camera_base.py
"""验证相机基类的锁与状态边界."""

import asyncio
import unittest
from typing import override

from perception.camera.base import BaseCamera, RawFrame
from storage.evidence import EvidenceStore

_FAKE_IMAGE = b"\x89PNG-fake"  # 证据柜只校验 media_type，不校验图片内容


class RecordingCamera(BaseCamera):
    """假相机：不碰硬件，只记录时间顺序和同时在采的峰值."""

    def __init__(self, _evidence: EvidenceStore, _delay_s: float = 0.02) -> None:
        """注入证据储存并初始化计数."""
        super().__init__(_evidence, "rec-cam")
        self._delay_s: float = _delay_s
        self.events: list[str] = []
        self.busy: int = 0
        self.max_busy: int = 0
        self.release_count: int = 0

    @override
    async def open_device(self) -> None:
        """假相机不需要打开相机."""

    @override
    async def grab_frame(self) -> RawFrame:
        """记录帧的开始与结束，并统计同时在采的峰值."""
        self.events.append("grab-start")
        self.busy += 1
        self.max_busy = max(self.max_busy, self.busy)
        await asyncio.sleep(self._delay_s)
        self.busy -= 1
        self.events.append("grab-end")
        return RawFrame(data=_FAKE_IMAGE, width=1, height=1, media_type="image/png")

    @override
    async def release_device(self) -> None:
        """记录释放，并数一共释放了几次."""
        self.events.append("release")
        self.release_count += 1


class BaseCameraLockTests(unittest.IsolatedAsyncioTestCase):
    """验证基类的锁：采集串行化，关闭采集，重复关闭只释放一次."""

    async def test_concurrent_captures_do_not_overlap(self) -> None:
        """同时发三次采集：同时在采的峰值必须是1."""
        camera = RecordingCamera(EvidenceStore())
        await camera.connect()

        _ = await asyncio.gather(
            camera.capture(0), camera.capture(0), camera.capture(0)
        )

        self.assertEqual(camera.max_busy, 1)

    async def test_two_closes_queued_behind_capture_release_once(self):
        """采集占着锁时来了两个 close ,等采集结束后只释放一次."""
        camera = RecordingCamera(EvidenceStore())
        await camera.connect()
        capture_task = asyncio.create_task(camera.capture(0))
        await asyncio.sleep(0.001)
        close_a = asyncio.create_task(camera.close())
        close_b = asyncio.create_task(camera.close())

        _ = await asyncio.gather(capture_task, close_a, close_b)

        self.assertEqual(camera.release_count, 1)

    async def test_reconnect_after_close_allows_capture(self) -> None:
        """关闭后重连：锁还能用，采集恢复正常."""
        camera = RecordingCamera(EvidenceStore())
        await camera.connect()
        await camera.close()
        await camera.connect()

        frame = await camera.capture(0)

        self.assertTrue(frame.frame_id)
        self.assertEqual(camera.events, ["release", "grab-start", "grab-end"])
