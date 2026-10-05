# tests/test_robot_adapter.py
"""验证设备能力边界和转向动作在真实 Runtime 中的生命周期."""

import math
import unittest
from typing import override

from domain.models import ActionRequest, ActionStatus, Pose2D, RobotState
from domain.robot import NavigationGoal, RobotCapability, UnsupportedCapabilityError
from robot.base import RobotAdapter
from robot.factory import RobotAdapterFactory
from robot.simulated import SimulatedAdapter, SimulationMode
from runtime.action_manager import ActionManager
from skills.registry import SkillRegistry
from skills.robot_registry import build_robot_skills
from skills.turn_relative import TurnRelativeSkill


class StationaryAdapter(RobotAdapter):
    """只有生命周期接口的设备不必实现不支持的运动."""

    @override
    async def connect(self) -> None:
        """测试占位连接."""

    @override
    async def disconnect(self) -> None:
        """测试占位释放."""

    @override
    async def get_state(self) -> RobotState:
        """返回无定位能力的连接快照，不用于运动验证."""
        return RobotState(True, False, Pose2D(), 0)

    @override
    async def stop(self) -> None:
        """测试占位停止."""


class RobotAdapterTests(unittest.IsolatedAsyncioTestCase):
    """不同设备的可选能力不会被误当作可执行动作."""

    async def test_optional_capabilities_fail_explicitly(self) -> None:
        """静态设备不注册运动，导航不降级为短时移动."""
        robot = StationaryAdapter()
        self.assertEqual(build_robot_skills(robot).names(), [])
        with self.assertRaises(UnsupportedCapabilityError):
            await robot.move_relative(0.1, 0.1)
        with self.assertRaises(UnsupportedCapabilityError):
            await robot.turn_relative(0.1, 0.1)
        with self.assertRaises(UnsupportedCapabilityError):
            await SimulatedAdapter().navigate_to(NavigationGoal("map", Pose2D()))
        async with ActionManager(
            robot, SkillRegistry([TurnRelativeSkill()])
        ) as runtime:
            with self.assertRaises(UnsupportedCapabilityError):
                _ = await runtime.submit_action(
                    ActionRequest(1, "turn_relative", {"angle_rad": 0.1})
                )
            self.assertEqual(runtime.list_actions(), [])

    async def test_turn_changes_following_motion_direction(self) -> None:
        """转向后前进沿新的朝向移动，位置和角度均通过验证."""
        robot = SimulatedAdapter()
        async with ActionManager(robot, build_robot_skills(robot)) as runtime:
            for skill, args in (
                ("turn_relative", {"angle_rad": math.pi / 2}),
                ("move_relative", {"distance_m": 0.2}),
            ):
                action = await runtime.submit_action(
                    ActionRequest(1, skill, dict[str, object](args))
                )
                result = await runtime.wait_for_action(action.action_id)
                self.assertEqual(result.status, ActionStatus.SUCCEEDED)
            pose = (await robot.get_state()).position
            self.assertAlmostEqual(pose.x, 0)
            self.assertAlmostEqual(pose.y, 0.2)
            self.assertAlmostEqual(pose.yaw, math.pi / 2)

    async def test_turn_timeout_and_failure_stop_device(self) -> None:
        """转向失败或超时均经 Runtime 清理并确认停止."""
        for mode, expected in (
            (SimulationMode.HANG, ActionStatus.TIMED_OUT),
            (SimulationMode.FAILURE, ActionStatus.FAILED),
        ):
            robot = SimulatedAdapter(_mode=mode)
            async with ActionManager(robot, build_robot_skills(robot)) as runtime:
                action = await runtime.submit_action(
                    ActionRequest(1, "turn_relative", {"angle_rad": 0.1}, 0.03)
                )
                self.assertEqual(
                    (await runtime.wait_for_action(action.action_id)).status, expected
                )
                self.assertFalse((await robot.get_state()).is_moving)

    async def test_turn_rejects_invalid_arguments(self) -> None:
        """非法角度与角速度在提交阶段被拒绝，不创建动作."""
        robot = SimulatedAdapter()
        async with ActionManager(robot, build_robot_skills(robot)) as runtime:
            for args in (
                {"angle_rad": True},
                {"angle_rad": math.inf},
                {"angle_rad": 4},
                {"angle_rad": 1, "speed_rad_s": 0},
                {"angle_rad": 1, "extra": 1},
            ):
                with self.assertRaises(ValueError):
                    _ = await runtime.submit_action(
                        ActionRequest(1, "turn_relative", dict[str, object](args))
                    )
            self.assertEqual(runtime.list_actions(), [])

    def test_factory_does_not_fallback_or_connect(self) -> None:
        """厂商构造器可注入，未知配置报错，不悄悄选择模拟器."""
        factory = RobotAdapterFactory()
        factory.register("stationary", StationaryAdapter)
        self.assertIsInstance(factory.create("stationary"), StationaryAdapter)
        self.assertIn(
            RobotCapability.TURN_RELATIVE, factory.create("simulated").capabilities
        )
        with self.assertRaises(ValueError):
            _ = factory.create("unknown")
        with self.assertRaises(ValueError):
            factory.register("simulated", StationaryAdapter)
        with self.assertRaises(ValueError):
            _ = NavigationGoal("", Pose2D())
