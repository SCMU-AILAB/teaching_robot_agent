# tests/test_scene_store.py
"""验证场景结论账本的记、查、清理与新旧裁决."""

import unittest

from domain.services import FrameReference, VisionAnalysis
from perception.scene_store import SceneStore


class SceneStoreTests(unittest.IsolatedAsyncioTestCase):
    """验证同一把钥匙的写入裁决、查询命中和旧版本清理."""

    def make_frame(
        self, _captured_at: float, _seq: int, _revision: int = 5
    ) -> FrameReference:
        """造一帧固定数据的引用，不依赖真实时钟.

        Args:
            _captured_at: 这一帧的采集时刻，测试里用固定数字.
            _seq: 帧序号，用来让 frame_id 和 evidence_id 各不相同.
            _revision: 这一帧属于第几版场景.

        Returns:
            可直接交给 remember 的帧引用.
        """
        return FrameReference(
            frame_id=f"cam0:{_seq}",
            camera_id="cam0",
            captured_at=_captured_at,
            width=640,
            height=480,
            evidence_id=f"ev-{_seq}",
            scene_revision=_revision,
        )

    async def remember_row(
        self,
        _store: SceneStore,
        _captured_at: float,
        _seq: int,
        _summary: str = "两个杯子",
        _question: str = "桌上有什么？",
        _revision: int = 5,
    ) -> None:
        """按同一把钥匙记一行，只暴露测试真正关心的那几样.

        Args:
            _store: 被测账本.
            _captured_at: 采集时刻，决定「谁更新」.
            _seq: 帧序号，让不同行的帧互不相同.
            _summary: 结论摘要，用来分辨账本里留下的是哪一行.
            _question: 问题，用来验证钥匙是否生效.
            _revision: 场景版本，用来验证旧版本清理.
        """
        await _store.remember(
            _question=_question,
            _frame=self.make_frame(_captured_at, _seq, _revision),
            _analysis=VisionAnalysis(_summary, "qwen2.5-vl", False),
            _analyzed_at=_captured_at + 0.5,
        )

    async def test_remember_then_find_returns_row(self) -> None:
        """记一行后可以用同一把钥匙把这一行查回来."""
        store = SceneStore()
        await self.remember_row(store, 1000.0, 1)

        row = store.find("桌上有什么？", "cam0", "qwen2.5-vl", 5)
        if row is None:
            self.fail("同一把钥匙应该能查回刚记下的那一行")
        self.assertEqual(row.captured_at, 1000.0)
        self.assertEqual(row.analysis.summary, "两个杯子")
        self.assertEqual(row.frame.frame_id, "cam0:1")

    async def test_question_whitespace_is_normalized(self) -> None:
        """问题首尾带空白也能用干净的问题查回来."""
        store = SceneStore()
        await self.remember_row(store, 1000.0, 1, _question="  桌上有什么？  ")

        self.assertIsNotNone(store.find("桌上有什么？", "cam0", "qwen2.5-vl", 5))

    async def test_other_key_does_not_hit(self) -> None:
        """钥匙四段里任意一段不同都查不到."""
        store = SceneStore()
        await self.remember_row(store, 1000.0, 1)

        for label, args in (
            ("换问题", ("第三排有人吗？", "cam0", "qwen2.5-vl", 5)),
            ("换相机", ("桌上有什么？", "cam9", "qwen2.5-vl", 5)),
            ("换模型", ("桌上有什么？", "cam0", "other-vlm", 5)),
            ("换版本", ("桌上有什么？", "cam0", "qwen2.5-vl", 6)),
        ):
            with self.subTest(label=label):
                self.assertIsNone(store.find(*args))

    async def test_newer_capture_overwrites(self) -> None:
        """同一把钥匙写第二次，采集更晚的那一行留下."""
        store = SceneStore()
        await self.remember_row(store, 1000.0, 1, _summary="旧的")
        await self.remember_row(store, 1001.0, 2, _summary="新的")

        row = store.find("桌上有什么？", "cam0", "qwen2.5-vl", 5)
        if row is None:
            self.fail("写过两行之后应该还能查到")
        self.assertEqual(row.analysis.summary, "新的")
        self.assertEqual(row.frame.frame_id, "cam0:2")

    async def test_older_capture_does_not_overwrite(self) -> None:
        """晚完成的旧帧不能覆盖新观察，这是合同 :86 的硬要求."""
        store = SceneStore()
        await self.remember_row(store, 1000.0, 2, _summary="先到的新的")
        await self.remember_row(store, 999.0, 1, _summary="后到的旧的")

        row = store.find("桌上有什么？", "cam0", "qwen2.5-vl", 5)
        if row is None:
            self.fail("写过两行之后应该还能查到")
        self.assertEqual(row.analysis.summary, "先到的新的")
        self.assertEqual(row.frame.frame_id, "cam0:2")

    async def test_drop_stale_removes_older_versions(self) -> None:
        """清理只删版本更旧的行，并返回删掉了几行."""
        store = SceneStore()
        await self.remember_row(store, 1000.0, 1, _revision=5)
        await self.remember_row(store, 1001.0, 2, _revision=6)

        self.assertEqual(store.drop_stale(6), 1)
        self.assertIsNone(store.find("桌上有什么？", "cam0", "qwen2.5-vl", 5))
        self.assertIsNotNone(store.find("桌上有什么？", "cam0", "qwen2.5-vl", 6))

    async def test_drop_stale_on_empty_store_returns_zero(self) -> None:
        """空账本清理时返回 0，不抛异常."""
        store = SceneStore()

        self.assertEqual(store.drop_stale(6), 0)
