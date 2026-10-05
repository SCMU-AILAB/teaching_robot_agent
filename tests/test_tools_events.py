# tests/test_tools_events.py
"""验证工具任务隔离、移动期间观察失效和事件广播."""

import asyncio
import unittest

from agent.tools import ToolAdapter
from app.team_gateway import TeamGateway
from domain.models import ActionEvent, ActionStatus
from education.knowledge import build_shapes_lesson
from education.service import EducationService
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter, SimulationMode
from runtime.action_manager import ActionManager
from runtime.events import ActionEventHub, EventSubscription
from skills.move_relative import MoveRelativeSkill
from skills.registry import SkillRegistry
from storage.evidence import EvidenceStore


class ToolsEventsTests(unittest.IsolatedAsyncioTestCase):
    """通过真实运行时验证工具和事件消费者的行为."""

    async def test_action_scope_and_stale_scene(self) -> None:
        """运动中观察标记过期，其他任务不能查询或取消动作."""
        async with ActionManager(
            SimulatedAdapter(_mode=SimulationMode.HANG),
            SkillRegistry([MoveRelativeSkill()]),
        ) as runtime:
            gateway = TeamGateway(runtime, SimulatedPerception(EvidenceStore()))
            education = EducationService(build_shapes_lesson())
            task = gateway.create_task("教学")
            other = gateway.create_task("其他任务")
            _ = education.start_session(task.task_id)
            tools = ToolAdapter(task.task_id, gateway, education)
            self.assertTrue(tools.lookup_knowledge("圆形"))
            action = await tools.submit_action("move_relative", {"distance_m": 0.1})
            self.assertTrue((await tools.observe_scene("物体")).stale)
            with self.assertRaises(KeyError):
                _ = await gateway.cancel_action(other.task_id, action.action_id)
            _ = await tools.cancel_action(action.action_id)
            result = await tools.wait_for_action(action.action_id)
            self.assertEqual(result.status, ActionStatus.CANCELLED)

    async def test_two_subscribers_receive_same_events(self) -> None:
        """展示与决策各收到完整状态序列，不竞争取走事件."""
        async with ActionManager(
            SimulatedAdapter(), SkillRegistry([MoveRelativeSkill()])
        ) as runtime:
            hub = ActionEventHub(runtime)
            try:
                async with hub.subscribe() as first, hub.subscribe() as second:
                    await hub.start()
                    gateway = TeamGateway(runtime, SimulatedPerception(EvidenceStore()))
                    task = gateway.create_task("移动")
                    action = await gateway.submit_action(
                        task.task_id, "move_relative", {"distance_m": 0.01}
                    )
                    _ = await gateway.wait_for_action(task.task_id, action.action_id)
                    async with asyncio.timeout(1):
                        for status in (
                            ActionStatus.QUEUED,
                            ActionStatus.RUNNING,
                            ActionStatus.VERIFYING,
                            ActionStatus.SUCCEEDED,
                        ):
                            self.assertEqual((await first.next_event()).status, status)
                            self.assertEqual((await second.next_event()).status, status)
            finally:
                await hub.close()

    async def test_slow_subscriber_and_shutdown_wake_waiters(self) -> None:
        """订阅溢出明确报错，关闭唤醒等待者，不无限等待."""
        subscription = EventSubscription(1)
        subscription.publish(ActionEvent(1, 1, ActionStatus.QUEUED))
        subscription.publish(ActionEvent(1, 1, ActionStatus.RUNNING))
        with self.assertRaises(RuntimeError):
            _ = await subscription.next_event()
        fresh = EventSubscription(1)
        waiter = asyncio.create_task(fresh.next_event())
        await asyncio.sleep(0)
        fresh.close()
        with self.assertRaises(EOFError):
            _ = await waiter
