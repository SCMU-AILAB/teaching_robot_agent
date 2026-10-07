# tests/test_robot_application.py
"""验证赛题主应用不依赖课程，串联观察、移动与安全知识."""

import asyncio
import unittest
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from agent.context import RobotContextBuilder
from agent.langchain_tools import build_langchain_tools
from agent.tools import ToolAdapter
from app.robot_application import RobotApplication
from domain.models import ActionStatus, TaskStatus
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter, SimulationMode
from storage.evidence import EvidenceStore


class RobotApplicationTests(unittest.IsolatedAsyncioTestCase):
    """用可控模型和真实业务工具验证通用任务路径."""

    def _app(self, model: FakeMessagesListChatModel) -> RobotApplication:
        """装配不包含 EducationService 的机器人应用."""
        return RobotApplication(
            SimulatedAdapter(), SimulatedPerception(EvidenceStore()), model
        )

    async def test_safety_question_without_classroom(self) -> None:
        """安全问答直接查询知识并完成，不创建课程或机器人动作."""
        model = FakeMessagesListChatModel(
            responses=[
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
                AIMessage("不要碰热水壶，需要取热水时请成年人帮助。"),
            ]
        )
        app = self._app(model)
        try:
            await app.start()
            task = await app.create_task("热水安全教育")
            adapter = ToolAdapter(task.task_id, app.gateway)
            names = {tool.name for tool in build_langchain_tools(adapter)}
            self.assertNotIn("evaluate_answer", names)
            self.assertNotIn("get_teaching_state", names)
            self.assertTrue(adapter.lookup_knowledge("热水"))
            with self.assertRaises(ValueError):
                _ = adapter.get_teaching_state()
            item = app.gateway.submit_text(task.task_id, "热水壶可以碰吗？")
            with patch.object(
                FakeMessagesListChatModel, "bind_tools", return_value=model
            ):
                reply = await app.process_next()
            self.assertEqual(reply.task_status, TaskStatus.COMPLETED)
            self.assertEqual(await app.host.handle(item), reply)
            self.assertEqual(app.runtime.list_actions(), [])
        finally:
            await app.close()

    async def test_observe_move_reobserve_and_finish(self) -> None:
        """观察到动作终态再到新观察形成闭环，旧画面失效且不触发课堂."""
        model = FakeMessagesListChatModel(
            responses=[
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "observe_scene",
                            "args": {"question": "左侧有什么？"},
                            "id": "o1",
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
                                "arguments": {"angle_rad": 0.1},
                            },
                            "id": "m",
                        }
                    ],
                ),
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "observe_scene",
                            "args": {"question": "转向后有什么？"},
                            "id": "o2",
                        }
                    ],
                ),
                AIMessage("这是模拟观察；模拟转向已完成。"),
            ]
        )
        app = self._app(model)
        try:
            await app.start()
            task = await app.create_task("观察周围并转向讲解")
            _ = app.gateway.submit_text(task.task_id, "观察左侧，转向后再观察")
            with patch.object(
                FakeMessagesListChatModel, "bind_tools", return_value=model
            ):
                reply = await app.process_next()
            self.assertEqual(reply.task_status, TaskStatus.COMPLETED)
            self.assertEqual(len(reply.action_ids), 1)
            self.assertEqual(
                app.runtime.list_actions()[0].status, ActionStatus.SUCCEEDED
            )
            context = await RobotContextBuilder(app.gateway).build(task.task_id)
            assert context.observation is not None
            self.assertEqual(context.observation.question, "转向后有什么？")
            self.assertFalse(context.observation.stale)
        finally:
            await app.close()

    async def test_observation_invalidated_on_movement(self) -> None:
        """移动使保存在统一上下文中的旧观察失效."""
        app = self._app(FakeMessagesListChatModel(responses=[AIMessage("unused")]))
        try:
            await app.start()
            task = await app.create_task("移动")
            tools = ToolAdapter(task.task_id, app.gateway)
            _ = await tools.observe_scene("桌面有什么？")
            _ = await tools.submit_action("turn_relative", {"angle_rad": 0.1})
            context = await RobotContextBuilder(app.gateway).build(task.task_id)
            assert context.observation is not None
            self.assertTrue(context.observation.stale)
        finally:
            await app.close()

    async def test_host_refreshes_scene_after_action(self) -> None:
        """模型不主动再观察时，宿主也会刷新已有场景证据再交回模型."""
        model = FakeMessagesListChatModel(
            responses=[
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "submit_action",
                            "args": {
                                "skill": "turn_relative",
                                "arguments": {"angle_rad": 0.1},
                            },
                            "id": "m",
                        }
                    ],
                ),
                AIMessage("模拟转向已完成。"),
            ]
        )
        app = self._app(model)
        try:
            await app.start()
            task = await app.create_task("观察后转向")
            before = await ToolAdapter(task.task_id, app.gateway).observe_scene("桌面")
            _ = app.gateway.submit_text(task.task_id, "转向")
            with patch.object(
                FakeMessagesListChatModel, "bind_tools", return_value=model
            ):
                _ = await app.process_next()
            after = app.gateway.latest_observation(task.task_id)
            assert after is not None
            self.assertNotEqual(before.frame.frame_id, after.frame.frame_id)
            self.assertFalse(after.stale)
            self.assertEqual(after.question, before.question)
        finally:
            await app.close()

    async def test_stop_does_not_wait_for_model(self) -> None:
        """普通机器人任务停止不经过教学逻辑，也不等待远端模型返回."""
        model = FakeMessagesListChatModel(responses=[AIMessage("unused")])
        app = self._app(model)
        entered = asyncio.Event()

        async def hang(*_args: object, **_kwargs: object) -> AIMessage:
            entered.set()
            _ = await asyncio.Event().wait()
            return AIMessage("unreachable")

        try:
            await app.start()
            task = await app.create_task("陪伴问答")
            _ = app.gateway.submit_text(task.task_id, "你好")
            with (
                patch.object(
                    FakeMessagesListChatModel, "bind_tools", return_value=model
                ),
                patch.object(FakeMessagesListChatModel, "ainvoke", side_effect=hang),
            ):
                worker = asyncio.create_task(app.process_next())
                _ = await entered.wait()
                async with asyncio.timeout(1):
                    result = await app.host.cancel(task.task_id)
                    with self.assertRaises(asyncio.CancelledError):
                        _ = await worker
                self.assertEqual(result.status, TaskStatus.CANCELLED)
        finally:
            await app.close()

    async def test_failed_motion_ends_task_without_retry(self) -> None:
        """模型请求的运动失败后不续接或自动重复动作."""
        model = FakeMessagesListChatModel(
            responses=[
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "submit_action",
                            "args": {
                                "skill": "move_relative",
                                "arguments": {"distance_m": 0.1},
                            },
                            "id": "m",
                        }
                    ],
                )
            ]
        )
        app = RobotApplication(
            SimulatedAdapter(_mode=SimulationMode.FAILURE),
            SimulatedPerception(EvidenceStore()),
            model,
        )
        try:
            await app.start()
            task = await app.create_task("移动")
            _ = app.gateway.submit_text(task.task_id, "前进")
            with patch.object(
                FakeMessagesListChatModel, "bind_tools", return_value=model
            ):
                reply = await app.process_next()
            self.assertEqual(reply.task_status, TaskStatus.FAILED)
            self.assertEqual(len(app.runtime.list_actions()), 1)
        finally:
            await app.close()

    async def test_action_limit_stops_instead_of_claiming_completion(self) -> None:
        """持续提交动作的模型达到上限后被停止，不把整体目标记为完成."""
        model = FakeMessagesListChatModel(
            responses=[
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
                )
            ]
        )
        app = self._app(model)
        try:
            await app.start()
            task = await app.create_task("重复转向")
            _ = app.gateway.submit_text(task.task_id, "转向")
            with patch.object(
                FakeMessagesListChatModel, "bind_tools", return_value=model
            ):
                reply = await app.process_next()
            self.assertEqual(reply.task_status, TaskStatus.CANCELLED)
            self.assertEqual(len(reply.action_ids), 3)
        finally:
            await app.close()
