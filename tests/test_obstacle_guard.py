# tests/test_obstacle_guard.py
"""避障守护：前进途中遇障、看不清、贴近障碍时停车；后退限距；经 Runtime 记为失败."""

import asyncio
import os
import time
import unittest
from typing import override
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from app.audio import build_simulated_audio
from app.depth import exploration_from_env, guard_from_env
from app.robot_application import RobotApplication
from domain.models import ActionRequest, ActionStatus
from perception.depth.geometry import Clearance
from perception.simulated import SimulatedPerception
from robot.obstacle_guard import GuardedRobotAdapter, ObstacleError
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.robot_registry import build_robot_skills
from storage.evidence import EvidenceStore
from tests.test_object_safety_flow import tool_call


class FakeDepth:
    """可由测试修改的空隙来源；fresh 为 False 时停止更新."""

    def __init__(self, ahead: float = 3.0) -> None:
        """初始为前方空旷."""
        self.ahead: float = ahead
        self.valid_ratio: float = 0.9
        self.fresh: bool = True
        self.started: bool = False
        self.closed: bool = False
        self.fail_start: bool = False
        self._last: Clearance | None = None

    def start(self) -> None:
        """模拟打开设备."""
        if self.fail_start:
            raise OSError("no device")
        self.started = True

    def close(self) -> None:
        """模拟关闭设备."""
        self.closed = True

    def latest(self) -> Clearance | None:
        """按当前设定生成空隙；不新鲜时返回上一次的结果."""
        if not self.started:
            return None
        if self.fresh or self._last is None:
            self._last = Clearance(time.time(), self.ahead, (), self.valid_ratio)
        return self._last


class GuardTests(unittest.IsolatedAsyncioTestCase):
    """真实时间驱动模拟底盘，单项约 0.5 秒."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """每个测试方法新建一套：模拟底盘按真实时间运动，1 米 / 0.5 米每秒约 2 秒."""
        super().__init__(_method_name)
        self.depth: FakeDepth = FakeDepth()
        self.inner: SimulatedAdapter = SimulatedAdapter(_time_scale=1.0)
        self.robot: GuardedRobotAdapter = GuardedRobotAdapter(self.inner, self.depth)

    @override
    async def asyncSetUp(self) -> None:
        """连接底盘与深度."""
        await self.robot.connect()

    @override
    async def asyncTearDown(self) -> None:
        """断开并关闭深度."""
        await self.robot.disconnect()
        self.assertTrue(self.depth.closed)

    async def _move_and_change(self, change: str) -> ObstacleError:
        """前进 1 米，0.3 秒后改变深度读数，返回守护抛出的错误."""
        move = asyncio.create_task(self.robot.move_relative(1.0, 0.5))
        await asyncio.sleep(0.3)
        if change == "obstacle":
            self.depth.ahead = 0.45
        elif change == "stale":
            self.depth.fresh = False
        elif change == "blank":
            self.depth.ahead = 0.55
            await asyncio.sleep(0.15)
            self.depth.valid_ratio = 0.2
        with self.assertRaises(ObstacleError) as caught:
            await move
        state = await self.robot.get_state()
        self.assertFalse(state.is_moving)
        self.assertLess(state.position.x, 0.6)
        return caught.exception

    async def test_obstacle_ahead_stops(self) -> None:
        """前方空隙小于停车距离加滑行量时停车."""
        error = await self._move_and_change("obstacle")
        self.assertIn("障碍", str(error))

    async def test_stale_depth_stops(self) -> None:
        """深度超过 0.5 秒未更新时停车."""
        error = await self._move_and_change("stale")
        self.assertIn("看不清", str(error))

    async def test_readings_vanish_when_too_close(self) -> None:
        """前方已近且中部有效读数骤减时视为贴近障碍."""
        self.robot = GuardedRobotAdapter(
            self.inner, self.depth, _stop_distance_m=0.1, _decel_m_s2=10.0
        )
        error = await self._move_and_change("blank")
        self.assertIn("障碍", str(error))

    async def test_clear_path_completes(self) -> None:
        """空旷时前进正常完成."""
        await self.robot.move_relative(0.2, 0.5)
        self.assertAlmostEqual((await self.robot.get_state()).position.x, 0.2, 3)

    async def test_blocked_before_start_sends_no_motion(self) -> None:
        """开始前已无空隙：直接拒绝，车不动."""
        self.depth.ahead = 0.3
        with self.assertRaises(ObstacleError):
            await self.robot.move_relative(0.5, 0.2)
        self.assertEqual((await self.robot.get_state()).position.x, 0.0)

    async def test_reverse_is_limited(self) -> None:
        """后退不检查前方，但单次不超过 0.3 米."""
        self.depth.ahead = 0.1
        await self.robot.move_relative(-0.2, 0.5)
        with self.assertRaises(ValueError):
            await self.robot.move_relative(-0.5, 0.5)

    async def test_turn_is_not_guarded(self) -> None:
        """前方有障碍时仍可原地转向."""
        self.depth.ahead = 0.1
        await self.robot.turn_relative(0.3, 1.0)
        self.assertAlmostEqual((await self.robot.get_state()).position.yaw, 0.3, 3)

    async def test_depth_start_failure_blocks_forward(self) -> None:
        """深度相机启动失败不阻止连接，但前进一律拒绝."""
        depth = FakeDepth()
        depth.fail_start = True
        robot = GuardedRobotAdapter(SimulatedAdapter(), depth)
        await robot.connect()
        try:
            with self.assertRaisesRegex(ObstacleError, "看不清"):
                await robot.move_relative(0.1, 0.1)
        finally:
            await robot.disconnect()


class GuardRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """经 Runtime 执行时遇障动作记为失败并带原因，执行层不锁定."""

    async def test_obstacle_fails_action_with_reason(self) -> None:
        """前方有障碍：move_relative 失败，原因含“前方有障碍物”."""
        depth = FakeDepth(ahead=0.3)
        robot = GuardedRobotAdapter(SimulatedAdapter(), depth)
        runtime = ActionManager(robot, build_robot_skills(robot))
        await runtime.start()
        try:
            record = await runtime.submit_action(
                ActionRequest(1, "move_relative", {"distance_m": 0.5})
            )
            result = await runtime.wait_for_action(record.action_id)
            self.assertEqual(result.status, ActionStatus.FAILED)
            assert result.failure is not None
            self.assertIn("前方有障碍物", result.failure)
            self.assertIsNone(runtime.blocked_reason)
        finally:
            await runtime.close()

    def test_reverse_limit_in_skill_help(self) -> None:
        """后退上限写进参数说明，超限在提交前就被拒绝."""
        robot = GuardedRobotAdapter(SimulatedAdapter(), FakeDepth())
        skill = build_robot_skills(robot).get("move_relative")
        self.assertIn("后退（负距离）最多 0.3 米", skill.parameter_help)
        with self.assertRaises(ValueError):
            skill.validate({"distance_m": -0.5})
        skill.validate({"distance_m": -0.3})

    async def test_blocked_move_reply_says_why_and_speaks(self) -> None:
        """遇障失败：回答说出原因并播报，任务继续."""
        depth = FakeDepth(ahead=0.3)
        robot = GuardedRobotAdapter(SimulatedAdapter(), depth)
        evidence = EvidenceStore()
        model = FakeMessagesListChatModel(
            responses=[
                tool_call(
                    "submit_action",
                    {"skill": "move_relative", "arguments": {"distance_m": 1.0}},
                    "m1",
                )
            ]
        )
        app = RobotApplication(
            robot,
            SimulatedPerception(evidence),
            model,
            _audio=build_simulated_audio(evidence),
            _evidence=evidence,
        )
        await app.start()
        try:
            task = await app.create_task("避障")
            with patch.object(
                FakeMessagesListChatModel, "bind_tools", return_value=model
            ):
                _ = app.gateway.submit_text(task.task_id, "往前走一米")
                reply = await app.process_next()
            self.assertEqual(reply.text, "前方有障碍物，已停下，换个方向试试吧。")
            snapshot = await app.gateway.get_snapshot(task.task_id)
            spoken = [
                a for a in snapshot.actions if a.raw_request.skill_name == "speak"
            ]
            self.assertEqual(len(spoken), 1)
            self.assertEqual(spoken[0].status, ActionStatus.SUCCEEDED)
        finally:
            await app.close()


class DepthEnvTests(unittest.TestCase):
    """环境配置：默认关闭，启用时必须给出安装参数."""

    def test_off_by_default(self) -> None:
        """未设置 DEPTH_CAMERA 时不包装、不开放探索."""
        robot = SimulatedAdapter()
        with patch.dict(os.environ, {"DEPTH_CAMERA": "off"}):
            self.assertIs(guard_from_env(robot), robot)
            self.assertIsNone(exploration_from_env())

    def test_realsense_requires_mount(self) -> None:
        """启用时缺少安装高度报错；齐全时得到守护与探索设置."""
        with patch.dict(os.environ, {"DEPTH_CAMERA": "realsense"}, clear=False):
            _ = os.environ.pop("DEPTH_CAMERA_HEIGHT_M", None)
            with self.assertRaises(ValueError):
                _ = guard_from_env(SimulatedAdapter())
        env = {
            "DEPTH_CAMERA": "realsense",
            "DEPTH_CAMERA_HEIGHT_M": "0.2",
            "DEPTH_CAMERA_PITCH_DEG": "8",
            "EXPLORE_RADIUS_M": "2",
        }
        with patch.dict(os.environ, env):
            self.assertIsInstance(
                guard_from_env(SimulatedAdapter()), GuardedRobotAdapter
            )
            config = exploration_from_env()
            assert config is not None
            self.assertEqual(config.radius_m, 2.0)
        with patch.dict(os.environ, {"DEPTH_CAMERA": "lidar"}):
            with self.assertRaises(ValueError):
                _ = exploration_from_env()


if __name__ == "__main__":
    _ = unittest.main()
