# tests/test_homing.py
"""回到出发点：识别叫回来的说法，按里程走回并转回原朝向，路上被挡如实说明."""

import math
import unittest
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from agent.homing import ALREADY_HOME_REPLY, ARRIVED_REPLY, wants_return
from app.robot_application import RobotApplication
from domain.models import ActionStatus
from perception.simulated import SimulatedPerception
from robot.base import RobotAdapter
from robot.obstacle_guard import GuardedRobotAdapter
from robot.simulated import SimulatedAdapter
from storage.evidence import EvidenceStore
from tests.test_obstacle_guard import FakeDepth


class WantsReturnTests(unittest.TestCase):
    """只认叫机器人回来的说法."""

    def test_return_requests(self) -> None:
        """常见叫回来的说法."""
        for text in (
            "回来",
            "你回来吧",
            "快回来呀",
            "回到原地",
            "回到我这里来",
            "回到刚才的地方",
            "返回起点",
            "你过来",
            "快过来",
            "到我这儿来",
        ):
            with self.subTest(text=text):
                self.assertTrue(wants_return(text))

    def test_not_return_requests(self) -> None:
        """孩子说自己回来、让机器人转过来看，都不是叫它走回去."""
        for text in (
            "我回来了",
            "等我回来再玩",
            "妈妈什么时候回来",
            "转过来看看我",
            "看过来",
            "往前走一点",
            "看看周围有什么",
        ):
            with self.subTest(text=text):
                self.assertFalse(wants_return(text))


class HomingTests(unittest.IsolatedAsyncioTestCase):
    """经宿主走回出发点."""

    def _app(self, robot: RobotAdapter) -> RobotApplication:
        """不装配语音：回答只返回文字."""
        evidence = EvidenceStore()
        return RobotApplication(
            robot,
            SimulatedPerception(evidence),
            FakeMessagesListChatModel(responses=[AIMessage("好呀。")]),
            _evidence=evidence,
        )

    async def _move(self, app: RobotApplication, task_id: int) -> None:
        """先走远：前进 1 米、左转 90°、再前进 0.5 米."""
        steps: tuple[tuple[str, dict[str, object]], ...] = (
            ("move_relative", {"distance_m": 1.0}),
            ("turn_relative", {"angle_rad": math.pi / 2}),
            ("move_relative", {"distance_m": 0.5}),
        )
        for skill, args in steps:
            record = await app.gateway.submit_action(task_id, skill, args, 10.0)
            result = await app.gateway.wait_for_action(task_id, record.action_id)
            self.assertEqual(result.status, ActionStatus.SUCCEEDED)

    async def test_walks_back_and_faces_original_heading(self) -> None:
        """走远后叫回来：回到出发点附近并转回原朝向，不经模型."""
        robot = SimulatedAdapter()
        app = self._app(robot)
        await app.start()
        try:
            task = await app.create_task("陪伴")
            await self._move(app, task.task_id)
            with patch.object(FakeMessagesListChatModel, "bind_tools") as bind:
                _ = app.gateway.submit_text(task.task_id, "你回来吧")
                reply = await app.process_next()
                bind.assert_not_called()
            self.assertEqual(reply.text, ARRIVED_REPLY)
            pose = (await app.gateway.get_snapshot(task.task_id)).robot.position
            self.assertLess(math.hypot(pose.x, pose.y), 0.15)
            self.assertLess(
                abs(math.atan2(math.sin(pose.yaw), math.cos(pose.yaw))), 0.1
            )
        finally:
            await app.close()

    async def test_already_home(self) -> None:
        """没走动过：不动，说就在出发的地方."""
        app = self._app(SimulatedAdapter())
        await app.start()
        try:
            task = await app.create_task("陪伴")
            _ = app.gateway.submit_text(task.task_id, "回来")
            reply = await app.process_next()
            self.assertEqual(reply.text, ALREADY_HOME_REPLY)
            snapshot = await app.gateway.get_snapshot(task.task_id)
            self.assertEqual(snapshot.actions, ())
        finally:
            await app.close()

    async def test_blocked_on_the_way_says_so(self) -> None:
        """回去的路被挡：停下，说还差多远，任务继续."""
        depth = FakeDepth()
        robot = GuardedRobotAdapter(SimulatedAdapter(), depth)
        app = self._app(robot)
        await app.start()
        try:
            task = await app.create_task("陪伴")
            await self._move(app, task.task_id)
            depth.ahead = 0.3
            _ = app.gateway.submit_text(task.task_id, "回到原地")
            reply = await app.process_next()
            self.assertIn("挡着", reply.text)
            self.assertIn("米", reply.text)
            snapshot = await app.gateway.get_snapshot(task.task_id)
            self.assertIsNone(snapshot.blocked_reason)
            self.assertEqual(snapshot.task.status.value, "running")
        finally:
            await app.close()
