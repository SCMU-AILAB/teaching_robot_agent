# tests/test_exploration.py
"""自由探索：范围边界、无路可走、新物体只讲一次、到时结束，以及宿主中的打断与停止."""

import asyncio
import math
import time
import unittest
from collections.abc import Sequence
from typing import override
from unittest.mock import patch

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.exploration import (
    EXPLORE_REPLY,
    ExplorationConfig,
    ExplorationController,
    same_object,
    wants_exploration,
)
from app.robot_application import RobotApplication
from app.team_gateway import TeamGateway
from domain.education import KnowledgePoint
from domain.models import Pose2D, TaskStatus
from education.knowledge_service import build_home_knowledge
from perception.depth.geometry import HEADINGS_RAD, Clearance
from robot.obstacle_guard import GuardedRobotAdapter
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.robot_registry import build_robot_skills
from storage.evidence import EvidenceStore
from tests.test_object_safety_flow import ObjectPerception


class TrackedAdapter(SimulatedAdapter):
    """记录每次前进后的位姿."""

    def __init__(self) -> None:
        """运动按 1% 真实时间进行."""
        super().__init__(_time_scale=0.01)
        self.stops: list[Pose2D] = []
        self.moves: int = 0

    @property
    def pose(self) -> Pose2D:
        """当前位姿."""
        return self._position

    @override
    async def move_relative(self, distance_m: float, speed_m_s: float) -> None:
        """前进后记录位姿."""
        await super().move_relative(distance_m, speed_m_s)
        self.moves += 1
        self.stops.append(self._position)


class World:
    """世界坐标中的障碍点；按车当前位姿给出各方向空隙."""

    def __init__(self, robot: TrackedAdapter, points: Sequence[tuple[float, float]]):
        """注入车与障碍点."""
        self.robot: TrackedAdapter = robot
        self.points: tuple[tuple[float, float], ...] = tuple(points)

    def start(self) -> None:
        """无设备."""

    def close(self) -> None:
        """无设备."""

    def latest(self) -> Clearance:
        """沿各方向找车身通道内最近的障碍点."""
        pose = self.robot.pose

        def clear(angle: float) -> float:
            direction = pose.yaw + angle
            best = 4.0
            for x, y in self.points:
                dx, dy = x - pose.x, y - pose.y
                along = dx * math.cos(direction) + dy * math.sin(direction)
                across = -dx * math.sin(direction) + dy * math.cos(direction)
                if along > 0 and abs(across) <= 0.2:
                    best = min(best, along)
            return best

        headings = tuple((angle, clear(angle)) for angle in HEADINGS_RAD)
        return Clearance(time.time(), clear(0.0), headings, 0.9)


def ring(radius: float) -> list[tuple[float, float]]:
    """围住原点的一圈障碍点."""
    return [
        (radius * math.cos(i * math.pi / 36), radius * math.sin(i * math.pi / 36))
        for i in range(72)
    ]


class Narration:
    """记录讲解请求的假讲解器."""

    def __init__(self) -> None:
        """空记录."""
        self.calls: list[list[str]] = []

    async def __call__(
        self, objects: Sequence[tuple[str, str]], entries: Sequence[KnowledgePoint]
    ) -> str:
        """返回提到的物体."""
        _ = entries
        labels = [label for label, _ in objects]
        self.calls.append(labels)
        return "我好像看到了" + "和".join(labels) + "。"


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    """直接运行探索控制器."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """组装模拟底盘、深度世界、感知与执行层."""
        super().__init__(_method_name)
        self.inner: TrackedAdapter = TrackedAdapter()
        self.world: World = World(self.inner, ())
        robot = GuardedRobotAdapter(self.inner, self.world)
        self.perception: ObjectPerception = ObjectPerception(
            EvidenceStore(), (("椅子", "中间"),)
        )
        self.runtime: ActionManager = ActionManager(robot, build_robot_skills(robot))
        self.gateway: TeamGateway = TeamGateway(self.runtime, self.perception)
        self.task_id: int = self.gateway.create_task("探索").task_id
        self.narration: Narration = Narration()

    @override
    async def asyncSetUp(self) -> None:
        """启动执行层."""
        await self.runtime.start()

    @override
    async def asyncTearDown(self) -> None:
        """关闭执行层."""
        await self.runtime.close()

    def controller(
        self, *, max_duration_s: float, radius_m: float = 3.0
    ) -> ExplorationController:
        """按时长与半径创建控制器，不播报."""
        return ExplorationController(
            self.gateway,
            self.task_id,
            self.world,
            self.narration,
            build_home_knowledge(),
            ExplorationConfig(radius_m=radius_m, max_duration_s=max_duration_s),
            _speak=False,
        )

    async def test_stays_within_radius_until_time_is_up(self) -> None:
        """空旷房间里走到时间结束，每一站都在离起点半径内."""
        summary = await self.controller(radius_m=1.0, max_duration_s=1.5).run()
        self.assertTrue(summary.startswith("探索时间到啦"))
        self.assertGreater(self.inner.moves, 2)
        for stop in self.inner.stops:
            self.assertLessEqual(math.hypot(stop.x, stop.y), 1.0 + 1e-6)

    async def test_boxed_in_turns_full_circle_then_ends(self) -> None:
        """四周 0.3 米都是障碍：原地转满一圈后结束，不前进."""
        self.world.points = tuple(ring(0.3))
        summary = await self.controller(max_duration_s=30).run()
        self.assertTrue(summary.startswith("周围都走不通了"))
        self.assertEqual(self.inner.moves, 0)
        self.assertAlmostEqual(abs(self.inner.pose.yaw), 2 * math.pi, delta=0.01)

    async def test_new_objects_told_once_and_risky_first(self) -> None:
        """名称变体视为同一物体只讲一次；资料库有条目的物体优先讲."""
        explorer = self.controller(max_duration_s=0.01)
        self.assertTrue(await explorer.run())
        self.perception.objects = (
            ("蓝色椅子", "左侧"),
            ("书", "中间"),
            ("插座", "右侧"),
        )
        explorer = self.controller(max_duration_s=0.01)
        explorer.seen = ["椅子"]
        _ = await explorer.run()
        self.assertEqual(self.narration.calls[0], ["椅子"])
        self.assertEqual(self.narration.calls[1][0], "插座")
        self.assertNotIn("蓝色椅子", self.narration.calls[1])

    async def test_obstacle_ahead_picks_open_side(self) -> None:
        """正前方 0.5 米有墙：第一步转向侧面再走."""
        self.world.points = tuple((0.5, y / 10) for y in range(-3, 4))
        _ = await self.controller(max_duration_s=0.05, radius_m=3.0).run()
        first = self.inner.stops[0]
        self.assertGreater(abs(first.y), 0.05)
        self.assertLess(first.x, 0.45)

    def test_exploration_intent(self) -> None:
        """明确的探索请求被识别，概念提问与普通移动不算."""
        for text in (
            "我们去探索一下吧！",
            "在房间里转转，看看有什么。",
            "带我到处逛逛吧",
        ):
            self.assertTrue(wants_exploration(text), text)
        for text in (
            "什么是探索？",
            "向左转一点点。",
            "往前走二十厘米。",
            "走到椅子那边",
        ):
            self.assertFalse(wants_exploration(text), text)

    def test_same_object(self) -> None:
        """互相包含视为同一物体."""
        self.assertTrue(same_object("椅子", "蓝色椅子"))
        self.assertFalse(same_object("椅子", "桌子"))
        self.assertFalse(same_object("", "桌子"))


class ExploreModel(BaseChatModel):
    """按消息内容回答：要求探索时调用工具，讲解请求回一句，其余回“好的。”."""

    @property
    @override
    def _llm_type(self) -> str:
        """模型类型标识."""
        return "explore-script"

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        """按最后一条消息与系统提示选择回复."""
        last = messages[-1]
        system = messages[0].text if isinstance(messages[0], SystemMessage) else ""
        if "正在房间里探索" in system:
            reply = AIMessage("我好像看到了新东西。")
        elif isinstance(last, ToolMessage):
            reply = AIMessage("好的，我开始探索啦。")
        elif "这个房间都有啥" in last.text:
            reply = AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "start_exploration",
                        "args": {"plan": "四处看看"},
                        "id": "e1",
                    }
                ],
            )
        else:
            reply = AIMessage("好的。")
        return ChatResult(generations=[ChatGeneration(message=reply)])


class HostExplorationTests(unittest.IsolatedAsyncioTestCase):
    """经主应用：探索在回答后开始，新输入打断，停止任务停车."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """模拟底盘外包空旷世界的深度守护."""
        super().__init__(_method_name)
        self.inner: TrackedAdapter = TrackedAdapter()
        self.inner.time_scale = 1.0
        robot = GuardedRobotAdapter(self.inner, World(self.inner, ()))
        self.model: ExploreModel = ExploreModel()
        self.app: RobotApplication = RobotApplication(
            robot,
            ObjectPerception(EvidenceStore(), (("椅子", "中间"),)),
            self.model,
            _exploration=ExplorationConfig(max_duration_s=60),
        )

    async def _start(self, text: str = "我们去探索一下吧") -> int:
        """建任务并让机器人开始探索，返回任务编号."""
        await self.app.start()
        task = await self.app.create_task("探索")
        with patch.object(ExploreModel, "bind_tools", return_value=self.model):
            _ = self.app.gateway.submit_text(task.task_id, text)
            reply = await self.app.process_next()
        expected = EXPLORE_REPLY if wants_exploration(text) else "好的，我开始探索啦。"
        self.assertEqual(reply.text, expected)
        self.assertTrue(self.app.host.exploring(task.task_id))
        # 等探索真正走起来
        for _ in range(100):
            if self.inner.pose.x != 0 or self.inner.pose.yaw != 0:
                break
            await asyncio.sleep(0.05)
        return task.task_id

    async def test_new_input_interrupts_exploration(self) -> None:
        """孩子说话：先停下探索与当前动作，再回答，任务继续."""
        task_id = await self._start()
        try:
            with patch.object(ExploreModel, "bind_tools", return_value=self.model):
                _ = self.app.gateway.submit_text(task_id, "你好")
                reply = await self.app.process_next()
            self.assertEqual(reply.text, "好的。")
            self.assertFalse(self.app.host.exploring(task_id))
            self.assertFalse((await self.inner.get_state()).is_moving)
            snapshot = await self.app.gateway.get_snapshot(task_id)
            self.assertEqual(snapshot.task.status, TaskStatus.RUNNING)
            self.assertTrue(all(a.status.is_terminal for a in snapshot.actions))
        finally:
            await self.app.close()

    async def test_model_tool_starts_exploration(self) -> None:
        """宿主没识别出的说法由模型调用探索工具启动."""
        self.assertFalse(wants_exploration("我想看看这个房间都有啥"))
        task_id = await self._start("我想看看这个房间都有啥")
        try:
            self.assertTrue(self.app.host.exploring(task_id))
        finally:
            await self.app.close()

    async def test_cancel_task_stops_exploration(self) -> None:
        """停止任务：探索结束、车停下、任务记为 cancelled."""
        task_id = await self._start()
        try:
            state = await self.app.cancel_task(task_id)
            self.assertEqual(state.status, TaskStatus.CANCELLED)
            self.assertFalse(self.app.host.exploring(task_id))
            self.assertFalse((await self.inner.get_state()).is_moving)
        finally:
            await self.app.close()

    async def test_tool_hidden_without_depth_guard(self) -> None:
        """底盘没有深度守护时不开放探索."""
        app = RobotApplication(
            SimulatedAdapter(),
            ObjectPerception(EvidenceStore(), ()),
            self.model,
            _exploration=ExplorationConfig(),
        )
        with self.assertRaises(ValueError):
            app.host.request_exploration(1)


if __name__ == "__main__":
    _ = unittest.main()
