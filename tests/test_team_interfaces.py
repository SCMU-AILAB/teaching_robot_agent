# tests/test_team_interfaces.py
"""验证队友协作接口的证据隔离、输入去重和取消边界."""

import asyncio
import unittest
from typing import override

from app.team_gateway import TeamGateway
from domain.services import ObservationRequest, ObservationResult, TranscriptResult
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.registry import SkillRegistry
from storage.evidence import EvidenceStore


class DelayedPerception(SimulatedPerception):
    """固定观察与任务取消的交错顺序."""

    def __init__(self, _evidence: EvidenceStore) -> None:
        """初始化可由测试释放的观察门."""
        super().__init__(_evidence)
        self.entered: asyncio.Event = asyncio.Event()
        self.release: asyncio.Event = asyncio.Event()

    @override
    async def observe(self, request: ObservationRequest) -> ObservationResult:
        """等待测试允许返回结果."""
        self.entered.set()
        _ = await self.release.wait()
        return await super().observe(request)


class TeamInterfaceTests(unittest.IsolatedAsyncioTestCase):
    """验证已实现的本地协作入口，不依赖模型或设备."""

    async def test_evidence_capacity_and_lookup(self) -> None:
        """容量溢出不覆盖已有媒体，路径字符串不能绕过资源登记."""
        store = EvidenceStore(_max_bytes=3)
        reference = await store.save(b"abc", "audio/wav", 1)
        self.assertEqual(await store.read(reference.evidence_id), b"abc")
        with self.assertRaises(ValueError):
            _ = await store.save(b"x", "audio/wav", 1)
        with self.assertRaises(KeyError):
            _ = await store.read("../../secret")
        self.assertEqual(store.get(reference.evidence_id).size_bytes, 3)

    async def test_transcript_deduplication_and_cancel(self) -> None:
        """最终转写只提交一次，空输入和已取消任务不进入教学队列."""
        perception = SimulatedPerception(EvidenceStore())
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(runtime, perception)
            task = gateway.create_task("测试教学")
            self.assertIsNone(
                gateway.submit_transcript(task.task_id, "empty", TranscriptResult(""))
            )
            first = gateway.submit_transcript(
                task.task_id, "one", TranscriptResult("杯子")
            )
            second = gateway.submit_transcript(
                task.task_id, "one", TranscriptResult("杯子")
            )
            self.assertEqual(first, second)
            self.assertEqual(await gateway.next_input(), first)
            with self.assertRaises(TimeoutError):
                async with asyncio.timeout(0.01):
                    _ = await gateway.next_input()
            with self.assertRaises(ValueError):
                _ = gateway.submit_transcript(
                    task.task_id, "one", TranscriptResult("不同结果")
                )
            _ = await gateway.cancel_task(task.task_id)
            with self.assertRaises(ValueError):
                _ = gateway.submit_text(task.task_id, "迟到输入")

    async def test_observation_has_real_evidence_reference(self) -> None:
        """模拟观察也必须能读取对应图片且明确标记模拟来源."""
        evidence = EvidenceStore()
        perception = SimulatedPerception(evidence)
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(runtime, perception)
            task = gateway.create_task("观察")
            result = await gateway.observe(
                ObservationRequest("one", task.task_id, "什么物体")
            )
            self.assertTrue(result.analysis.simulated)
            self.assertTrue(
                (await evidence.read(result.frame.evidence_id)).startswith(b"\x89PNG")
            )
            self.assertEqual(perception.invalidate("移动"), 1)
            updated = await gateway.observe(
                ObservationRequest("two", task.task_id, "什么物体")
            )
            self.assertEqual(updated.frame.scene_revision, 1)

    async def test_cancelled_task_rejects_late_observation(self) -> None:
        """已经取消的任务不得被迟到视觉结果继续驱动."""
        perception = DelayedPerception(EvidenceStore())
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(runtime, perception)
            task = gateway.create_task("观察")
            pending = asyncio.create_task(
                gateway.observe(ObservationRequest("one", task.task_id, "观察"))
            )
            _ = await perception.entered.wait()
            _ = await gateway.cancel_task(task.task_id)
            perception.release.set()
            with self.assertRaises(ValueError):
                _ = await pending
