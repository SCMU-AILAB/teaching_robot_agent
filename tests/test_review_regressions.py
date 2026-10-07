# tests/test_review_regressions.py
"""覆盖代码审查发现的取消、证据、续接与资源保留故障."""

import asyncio
import io
import time
import unittest
from dataclasses import replace
from typing import override
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from PIL import Image

from agent.context import RobotContextBuilder
from agent.tools import ToolAdapter
from app.robot_application import RobotApplication
from domain.models import (
    ActionRequest,
    ActionStatus,
    RobotState,
    SkillResult,
    TaskStatus,
)
from domain.services import ObservationRequest
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.move_relative import MoveRelativeSkill
from skills.registry import SkillRegistry
from storage.evidence import EvidenceStore
from storage.memory import InMemoryStore


class CancelledStopAdapter(SimulatedAdapter):
    """停止驱动自身取消一次，随后允许测试关闭设备."""

    def __init__(self) -> None:
        """初始化一次性故障."""
        super().__init__()
        self.fail_stop: bool = True

    @override
    async def stop(self) -> None:
        """复现 SDK 清理抛出取消异常."""
        if self.fail_stop:
            self.fail_stop = False
            raise asyncio.CancelledError("SDK stop cancelled")
        await super().stop()


class StaleAdapter(SimulatedAdapter):
    """始终提供旧的静止遥测."""

    @override
    async def get_state(self) -> RobotState:
        """保留位姿，篡改采集时间以模拟反馈停滞."""
        return replace(await super().get_state(), updated_at=1.0)


class ReviewRegressionTests(unittest.IsolatedAsyncioTestCase):
    """使用确定性替身验证审查中的实际故障路径."""

    def make_app(self, model: FakeMessagesListChatModel) -> RobotApplication:
        """装配无外部连接的应用."""
        return RobotApplication(
            SimulatedAdapter(), SimulatedPerception(EvidenceStore()), model
        )

    async def test_cleanup_cancellation_finalizes_and_blocks_queue(self) -> None:
        """清理取消必须落终态，排队动作不得继续执行."""
        runtime = ActionManager(
            CancelledStopAdapter(), SkillRegistry([MoveRelativeSkill()])
        )
        async with runtime:
            request = ActionRequest(1, "move_relative", {"distance_m": 0.01})
            first = await runtime.submit_action(request)
            second = await runtime.submit_action(request)
            async with asyncio.timeout(2):
                results = await asyncio.gather(
                    runtime.wait_for_action(first.action_id),
                    runtime.wait_for_action(second.action_id),
                )
            self.assertTrue(all(r.status == ActionStatus.FAILED for r in results))
            self.assertIn("CancelledError", results[0].failure or "")
            self.assertIsNone(results[1].started_at)
            self.assertTrue(runtime.blocked_reason)
            with self.assertRaises(RuntimeError):
                _ = await runtime.submit_action(request)

    async def test_cancel_before_consumer_and_next_request(self) -> None:
        """提前停止不让消费者永远等待，下一任务仍能处理."""
        model = FakeMessagesListChatModel(responses=[AIMessage("你好")])
        app = self.make_app(model)
        try:
            await app.start()
            task = await app.create_task("提前停止")
            _ = app.gateway.submit_text(task.task_id, "你好")
            _ = await app.host.cancel(task.task_id)
            async with asyncio.timeout(1):
                reply = await app.process_next()
            self.assertTrue(reply.rejected)
            self.assertEqual(reply.task_status, TaskStatus.CANCELLED)
            task = await app.create_task("下一任务")
            _ = app.gateway.submit_text(task.task_id, "你好")
            with patch.object(
                FakeMessagesListChatModel, "bind_tools", return_value=model
            ):
                async with asyncio.timeout(1):
                    reply = await app.process_next()
            self.assertFalse(reply.rejected)
        finally:
            await app.close()

    async def test_plan_text_keeps_task_open_for_clarification(self) -> None:
        """动作预告不能完成任务，同任务可继续澄清并显式验收."""
        model = FakeMessagesListChatModel(
            responses=[AIMessage("我将观察。"), AIMessage("请说明方向。")]
        )
        app = self.make_app(model)
        try:
            await app.start()
            task = await app.create_task("观察并讲解")
            with patch.object(
                FakeMessagesListChatModel, "bind_tools", return_value=model
            ):
                for text in ("看看周围", "需要什么信息？"):
                    _ = app.gateway.submit_text(task.task_id, text)
                    reply = await app.process_next()
                    self.assertNotEqual(reply.task_status, TaskStatus.COMPLETED)
            self.assertEqual(
                (await app.finish_task(task.task_id)).status, TaskStatus.COMPLETED
            )
        finally:
            await app.close()

    async def test_old_telemetry_cannot_confirm_arrival_or_stop(self) -> None:
        """静止且位姿正确的旧遥测也不能证明完成或停止."""
        robot = StaleAdapter()
        await robot.connect()
        skill = MoveRelativeSkill()
        try:
            with self.assertRaises(RuntimeError):
                await skill.check_preconditions(robot)
            result = SkillResult(
                "旧反馈",
                {
                    "commanded_at": time.time(),
                    "expected_position": {"x": 0.0, "y": 0.0, "yaw": 0.0},
                },
            )
            self.assertFalse(await skill.verify(robot, result))
            with self.assertRaises(RuntimeError):
                await skill.cleanup(robot)
            fresh = replace(await robot.get_state(), updated_at=time.time())
            with self.assertRaises(RuntimeError):
                robot.validate_state(fresh, after=fresh.updated_at + 0.01)
        finally:
            await robot.disconnect()

    async def test_explicit_observation_cannot_bypass_invalidation(self) -> None:
        """旧对象不能覆盖运动失效标记或后续观察."""
        app = self.make_app(FakeMessagesListChatModel(responses=[AIMessage("unused")]))
        try:
            await app.start()
            task = await app.create_task("观察")
            tools = ToolAdapter(task.task_id, app.gateway)
            old = await tools.observe_scene("原观察")
            action = await tools.submit_action("turn_relative", {"angle_rad": 0.01})
            context = await RobotContextBuilder(app.gateway).build(
                task.task_id, observation=old
            )
            assert context.observation is not None
            self.assertTrue(context.observation.stale)
            _ = await app.gateway.wait_for_action(task.task_id, action.action_id)
            new = await tools.observe_scene("新观察")
            context = await RobotContextBuilder(app.gateway).build(
                task.task_id, observation=old
            )
            self.assertEqual(context.observation, new)
        finally:
            await app.close()

    async def test_resume_preserves_input_knowledge_and_action_result(self) -> None:
        """续接包含原输入、知识来源和与动作调用配对的结果."""
        model = FakeMessagesListChatModel(responses=[AIMessage("unused")])
        responses = iter(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "lookup_knowledge",
                            "args": {"query": "热水"},
                            "id": "k",
                        }
                    ],
                ),
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "submit_action",
                            "args": {
                                "skill": "turn_relative",
                                "arguments": {"angle_rad": 0.01},
                            },
                            "id": "m",
                        }
                    ],
                ),
                AIMessage("已完成模拟转向。"),
            ]
        )
        captured: list[list[BaseMessage]] = []

        async def invoke(messages: list[BaseMessage]) -> AIMessage:
            """记录真正发给模型的消息序列."""
            captured.append(messages)
            return next(responses)

        app = self.make_app(model)
        try:
            await app.start()
            task = await app.create_task("安全讲解")
            _ = app.gateway.submit_text(task.task_id, "查询热水知识，然后左转讲解")
            with (
                patch.object(
                    FakeMessagesListChatModel, "bind_tools", return_value=model
                ),
                patch.object(FakeMessagesListChatModel, "ainvoke", side_effect=invoke),
            ):
                _ = await app.process_next()
            last = captured[-1]
            self.assertIn("查询热水知识，然后左转讲解", str(last))
            results = {
                m.tool_call_id: m.content for m in last if isinstance(m, ToolMessage)
            }
            self.assertIn("热水", str(results["k"]))
            self.assertIn("succeeded", str(results["m"]))
        finally:
            await app.close()

    async def test_simulated_png_and_evidence_release(self) -> None:
        """模拟媒体能够校验和解码，显式释放回收容量."""
        evidence = EvidenceStore(_max_bytes=128)
        perception = SimulatedPerception(evidence)
        observation = await perception.observe(ObservationRequest("o", 1, "物体"))
        identifier = observation.frame.evidence_id
        content = await evidence.read(identifier)
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            _ = image.load()
            self.assertEqual(image.size, (1, 1))
        evidence.release(identifier)
        evidence.release(identifier)
        with self.assertRaises(KeyError):
            _ = await evidence.read(identifier)
        _ = await evidence.save(content, "image/png", time.time())
        await perception.close()

    async def test_application_drains_events_and_store_is_bounded(self) -> None:
        """主应用广播器消费动作事件，记录容量满后明确拒绝."""
        app = self.make_app(FakeMessagesListChatModel(responses=[AIMessage("unused")]))
        try:
            await app.start()
            task = await app.create_task("事件")
            async with app.events.subscribe() as subscription:
                action = await ToolAdapter(task.task_id, app.gateway).submit_action(
                    "turn_relative", {"angle_rad": 0.01}
                )
                async with asyncio.timeout(2):
                    while True:
                        event = await subscription.next_event()
                        if event.status.is_terminal:
                            break
                self.assertEqual(event.action_id, action.action_id)
                self.assertEqual(event.status, ActionStatus.SUCCEEDED)
        finally:
            await app.close()
        store = InMemoryStore(_max_records=1)
        _ = store.create_task("一")
        with self.assertRaises(RuntimeError):
            _ = store.create_task("二")
        request = ActionRequest(1, "move_relative", {"distance_m": 0})
        _ = store.create_action(request)
        with self.assertRaises(RuntimeError):
            _ = store.create_action(request)
