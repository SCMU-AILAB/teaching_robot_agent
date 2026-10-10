# tests/test_perception_service.py
"""验证管家的各项功能."""

import asyncio
import time
import unittest
from collections.abc import Callable
from typing import override

from domain.services import (
    FrameReference,
    ObservationRequest,
    VisionAnalysis,
    VisionRequest,
)
from perception.perception_service import LivePerception
from perception.scene_store import SceneStore


class FakeCamera:
    """假相机：不碰硬件，只数采集了几次."""

    def __init__(self) -> None:
        """初始化计数器和关闭标志."""
        self.captures: int = 0
        self.closed: bool = False
        self.close_calls: int = 0

    async def connect(self) -> None:
        """假相机不需要连接."""

    async def capture(self, scene_revision: int) -> FrameReference:
        """造一个帧，并把版本号原样写进去."""
        self.captures += 1
        return FrameReference(
            frame_id=f"frame-{self.captures}",
            camera_id="cam-1",
            captured_at=time.time(),
            width=4,
            height=4,
            evidence_id=f"ev-{self.captures}",
            scene_revision=scene_revision,
        )

    async def close(self) -> None:
        """记下被关过."""
        self.close_calls += 1
        self.closed = True


class FakeProvider:
    """假模型：不碰网络，只数推理了几次."""

    def __init__(self, _delay_s: float = 0.0) -> None:
        """初始化计数器."""
        self.analyzes: int = 0
        self.delay_s: float = _delay_s
        self.requests: list[VisionRequest] = []
        self.interrupted: bool = False
        self.during: Callable[[], None] | None = None

    async def analyze(self, request: VisionRequest) -> VisionAnalysis:
        """返回一个固定结论，并记下这次推理."""
        self.analyzes += 1
        self.requests.append(request)
        if self.during is not None:
            self.during()
        try:
            await asyncio.sleep(self.delay_s)
        except asyncio.CancelledError:
            self.interrupted = True
            raise
        return VisionAnalysis("假结论", "qwen3.5:9b", False)


class SpyStore(SceneStore):
    """记账本：转发给真账本，同时数 remember 被调了几次."""

    def __init__(self) -> None:
        """初始化计数器."""
        super().__init__()
        self.remembered: int = 0

    @override
    async def remember(
        self,
        _question: str,
        _frame: FrameReference,
        _analysis: VisionAnalysis,
        _analyzed_at: float,
    ) -> None:
        """记数之后交给真账本."""
        self.remembered += 1
        await super().remember(_question, _frame, _analysis, _analyzed_at)


class LivePreceptionTests(unittest.IsolatedAsyncioTestCase):
    """验证管家的缓存,失效,超时与关闭行为."""

    def make_service(
        self, _provider_delay_s: float = 0.0
    ) -> tuple[FakeCamera, FakeProvider, SpyStore, LivePerception]:
        """造一整套假对象并接线，返回给用例逐个断言."""
        camera = FakeCamera()
        provider = FakeProvider(_provider_delay_s)
        store = SpyStore()
        service = LivePerception(camera, provider, store, "cam-1", "qwen3.5:9b")
        return camera, provider, store, service

    async def test_first_observation_captures_and_records(self) -> None:
        """首次观察应真实采集、真实推理，并写入账本一次."""
        camera, provider, store, service = self.make_service()

        result = await service.observe(ObservationRequest("o1", 7, "桌上有什么"))

        self.assertFalse(result.from_cache)  # 不是缓存
        self.assertFalse(result.stale)  # 也不是历史结果
        self.assertEqual(camera.captures, 1)  # 相机真的拍了一次
        self.assertEqual(provider.analyzes, 1)  # 模型真的推理了一次
        self.assertEqual(store.remembered, 1)  # 账本真的写了一行

    async def test_second_ask_reuses_cache(self) -> None:
        """第二次问同一个问题应复用账本：不再拍照、不再推理、不再记账."""
        camera, provider, store, service = self.make_service()

        result1 = await service.observe(ObservationRequest("o1", 7, "桌上有什么"))
        result2 = await service.observe(ObservationRequest("o2", 8, "桌上有什么"))

        self.assertFalse(result1.from_cache)
        self.assertTrue(result2.from_cache)
        self.assertFalse(result1.stale)
        self.assertFalse(result2.stale)
        self.assertEqual(camera.captures, 1)  # 没有重新拍照
        self.assertEqual(provider.analyzes, 1)  # 没有重新推理
        self.assertEqual(store.remembered, 1)  # 没有重复记账
        self.assertEqual(result1.analyzed_at, result2.analyzed_at)
        self.assertEqual(result1.frame.captured_at, result2.frame.captured_at)

    async def test_moved_scene_does_not_reuse_old_cache(self) -> None:
        """移动失效之后，同一个问题不得命中旧版本的缓存."""
        camera, provider, store, service = self.make_service()

        _ = await service.observe(ObservationRequest("o1", 7, "桌上有什么"))
        revision = service.invalidate("机器移动")
        second = await service.observe(ObservationRequest("o2", 7, "桌上有什么"))

        self.assertFalse(second.from_cache)
        self.assertEqual(camera.captures, 2)
        self.assertFalse(second.stale)
        self.assertEqual(second.frame.scene_revision, revision)
        self.assertEqual(provider.analyzes, 2)
        self.assertEqual(store.remembered, 2)

    async def test_zero_max_age_forces_fresh_capture(self) -> None:
        """max_age_s=0 应强制重新采集：即使账本里刚写了一份也不许复用."""
        camera, provider, _, service = self.make_service()
        first = await service.observe(ObservationRequest("o1", 7, "桌上有什么"))
        second = await service.observe(ObservationRequest("o1", 7, "桌上有什么", 0))

        self.assertFalse(first.from_cache)  # 第一次必然是真采集
        self.assertFalse(second.from_cache)  # ★ age=0 → 不许命中
        self.assertEqual(camera.captures, 2)  # ★ 这才是「强制采集」的证据
        self.assertEqual(provider.analyzes, 2)  # 而且重新推理了一遍

    async def test_different_question_does_not_reuse_cache(self) -> None:
        """换了问题就不该命中缓存：必须重新采集."""
        camera, provider, _, service = self.make_service()

        first = await service.observe(ObservationRequest("o1", 7, "桌上有什么"))
        second = await service.observe(ObservationRequest("o2", 7, "桌上有几个人"))

        self.assertFalse(first.from_cache)
        self.assertFalse(second.from_cache)
        self.assertEqual(camera.captures, 2)
        self.assertEqual(provider.analyzes, 2)

    async def test_invalidate_during_analysis_marks_result_stale(self) -> None:
        """推理期间发生失效时，结果应标记 stale 且不写入账本."""
        camera, provider, store, service = self.make_service()

        def robot_moved() -> None:
            _ = service.invalidate("机器移动")

        provider.during = robot_moved  # ★ 必须在 observe 之前装好
        result = await service.observe(
            ObservationRequest("o1", 7, "桌上有什么", max_age_s=0.0)
        )

        self.assertFalse(result.from_cache)
        self.assertTrue(result.stale)  # 结果作废
        self.assertEqual(store.remembered, 0)  # ★ 闸门拦住：一个字都没记账
        self.assertEqual(camera.captures, 1)

    async def test_slow_model_raises_timeout(self) -> None:
        """模型超过总时限时应抛出 TimeoutError，且不留残余任务."""
        before = set(asyncio.all_tasks())  # ★ 记住基线，用来查「有没有留下没结束的活」
        camera, _, store, service = self.make_service(_provider_delay_s=5.0)

        with self.assertRaises(TimeoutError):
            _ = await service.observe(
                ObservationRequest("o1", 7, "桌上有什么", timeout_s=0.1)
            )

        self.assertEqual(camera.captures, 1)  # 照片已经拍过了
        self.assertEqual(store.remembered, 0)  # 超时了 → 不许记账
        self.assertFalse(asyncio.all_tasks() - before - {asyncio.current_task()})

    async def test_close_cancels_in_flight_observation(self) -> None:
        """关闭应取消在途观察，而不是等它自然跑完."""
        camera, provider, _, service = self.make_service(_provider_delay_s=5.0)
        started = asyncio.Event()
        provider.during = started.set  # ★ 推理一开始就发信号（本身是同步方法）

        task = asyncio.create_task(
            service.observe(ObservationRequest("o1", 7, "桌上有什么"))
        )
        _ = await started.wait()  # ★ 确定它已经进到推理里 —— 不靠 sleep

        tick = time.monotonic()
        await service.close()
        elapsed = time.monotonic() - tick

        self.assertLess(elapsed, 1.0)  # ★ 没有干等那 5 秒
        self.assertTrue(task.cancelled())  # ★ 任务真的被取消
        self.assertTrue(provider.interrupted)  # ★ 取消打进了推理那一行
        self.assertEqual(camera.close_calls, 1)  # 相机被关了一次

    async def test_close_releases_camera_and_is_idempotent(self) -> None:
        """关闭应释放相机，且重复调用安全."""
        camera, _, _, service = self.make_service()

        await service.close()
        await service.close()  # 第二次不该抛

        self.assertTrue(camera.closed)
        self.assertEqual(camera.close_calls, 1)  # ★ 只关一次，不是关两次

    async def test_observe_after_close_raises(self) -> None:
        """关闭之后再观察应抛出 RuntimeError."""
        camera, _, _, service = self.make_service()
        await service.close()

        with self.assertRaises(RuntimeError):
            _ = await service.observe(ObservationRequest("o1", 7, "桌上有什么"))

        self.assertEqual(camera.captures, 0)  # 也不该再去拍照
