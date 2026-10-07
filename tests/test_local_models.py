# tests/test_local_models.py
"""验证本地视觉适配及模型工具调用的输入、取消和执行边界."""

import asyncio
import io
import os
import time
import unittest
from dataclasses import replace
from typing import override
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from PIL import Image

from agent.context import ContextBuilder
from agent.embodied_agent import EmbodiedAgent
from agent.tools import ToolAdapter
from app.team_gateway import TeamGateway
from domain.models import ActionStatus
from domain.services import FrameReference, ServiceError, VisionRequest
from education.knowledge import build_shapes_lesson
from education.service import EducationService
from perception.simulated import SimulatedPerception
from providers.local_model import build_local_model
from providers.ollama_vision import OllamaVisionProvider
from robot.simulated import SimulatedAdapter, SimulationMode
from runtime.action_manager import ActionManager
from skills.move_relative import MoveRelativeSkill
from skills.registry import SkillRegistry
from storage.evidence import EvidenceStore


class LocalVisionTests(unittest.IsolatedAsyncioTestCase):
    """视觉输出经过校验，失败不伪装成有效观察."""

    async def _request(self, store: EvidenceStore) -> VisionRequest:
        """创建有效图片及与其一致的采集元数据."""
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
        evidence = await store.save(buffer.getvalue(), "image/png", time.time())
        frame = FrameReference(
            "frame", "camera", evidence.captured_at, 8, 8, evidence.evidence_id, 0
        )
        return VisionRequest("request", "什么颜色？", frame, 1)

    async def test_valid_and_malformed_response(self) -> None:
        """有效文字可用，额外字段、空文本和非 JSON 输出均被拒绝."""
        store = EvidenceStore()
        request = await self._request(store)
        for text in (
            '{"summary":"红色"}',
            '{"summary":"  "}',
            '{"summary":"红色", "action":"move"}',
            "图片是红色的",
        ):
            with self.subTest(text=text):
                model = FakeMessagesListChatModel(responses=[AIMessage(text)])
                provider = OllamaVisionProvider(model, "test-vlm", store)
                if text == '{"summary":"红色"}':
                    result = await provider.analyze(request)
                    self.assertEqual(result.summary, "红色")
                    self.assertFalse(result.simulated)
                    self.assertEqual(result.objects, ())
                else:
                    with self.assertRaises(ServiceError):
                        _ = await provider.analyze(request)

    async def test_corrupt_media_and_metadata(self) -> None:
        """损坏图片、尺寸不符和错误采集时间不能进入推理."""
        store = EvidenceStore()
        request = await self._request(store)
        model = FakeMessagesListChatModel(responses=[AIMessage('{"summary":"红色"}')])
        provider = OllamaVisionProvider(model, "test-vlm", store)
        for frame in (
            replace(request.frame, width=9),
            replace(request.frame, captured_at=0),
        ):
            with self.assertRaises(ValueError):
                _ = await provider.analyze(replace(request, frame=frame))
        bad = await store.save(b"not an image", "image/png", request.frame.captured_at)
        with self.assertRaises(OSError):
            _ = await provider.analyze(
                replace(
                    request, frame=replace(request.frame, evidence_id=bad.evidence_id)
                )
            )

    async def test_timeout_and_cancellation_propagate(self) -> None:
        """超时和取消向宿主传播，不包装为成功结果或普通模型错误."""
        store = EvidenceStore()
        request = await self._request(store)
        model = FakeMessagesListChatModel(responses=[AIMessage("unused")])
        provider = OllamaVisionProvider(model, "test-vlm", store)
        entered = asyncio.Event()

        async def wait_forever(*_args: object, **_kwargs: object) -> AIMessage:
            entered.set()
            _ = await asyncio.Event().wait()
            return AIMessage("unreachable")

        with patch.object(
            FakeMessagesListChatModel, "ainvoke", side_effect=wait_forever
        ):
            with self.assertRaises(TimeoutError):
                _ = await provider.analyze(replace(request, timeout_s=0.05))
            entered.clear()
            task = asyncio.create_task(provider.analyze(request))
            _ = await entered.wait()
            _ = task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                _ = await task

    def test_configuration_requires_explicit_origin(self) -> None:
        """部署地址必须配置，拒绝地址内凭据且不改变全局代理."""
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                _ = build_local_model()
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://user:pass@localhost"}):
            with self.assertRaises(ValueError):
                _ = build_local_model()


class LocalAgentTests(unittest.IsolatedAsyncioTestCase):
    """脚本化模型驱动真实业务工具，测试不依赖服务器可用性."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """创建测试依赖，不在构造期间连接设备."""
        super().__init__(_method_name)
        self.runtime: ActionManager = ActionManager(
            SimulatedAdapter(_mode=SimulationMode.HANG),
            SkillRegistry([MoveRelativeSkill()]),
        )
        self.gateway: TeamGateway = TeamGateway(
            self.runtime, SimulatedPerception(EvidenceStore())
        )
        self.education: EducationService = EducationService(build_shapes_lesson())
        self.task_id: int = self.gateway.create_task("教学").task_id
        _ = self.education.start_session(self.task_id)

    @override
    async def asyncSetUp(self) -> None:
        """在测试事件循环中启动模拟执行层."""
        await self.runtime.start()

    @override
    async def asyncTearDown(self) -> None:
        """取消未结束的模拟动作并释放执行层."""
        await self.runtime.close()

    def _agent(self, model: FakeMessagesListChatModel) -> EmbodiedAgent:
        """构造绑定当前任务的模型决策器."""
        return EmbodiedAgent(
            model,
            ContextBuilder(self.gateway, self.education),
            ToolAdapter(self.task_id, self.gateway, self.education),
            self.task_id,
        )

    async def test_knowledge_tool_then_answer(self) -> None:
        """模型查询知识后继续生成文本，且不产生动作."""
        model = FakeMessagesListChatModel(
            responses=[
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "lookup_knowledge",
                            "args": {"query": "圆形"},
                            "id": "1",
                        }
                    ],
                ),
                AIMessage("圆形没有角。"),
            ]
        )
        with patch.object(FakeMessagesListChatModel, "bind_tools", return_value=model):
            reply = await self._agent(model).decide()
        self.assertEqual(reply.text, "圆形没有角。")
        self.assertIsNone(reply.pending_action_id)
        self.assertEqual(self.runtime.list_actions(self.task_id), [])

    async def test_action_yields_without_polling(self) -> None:
        """接受动作即返回编号，不等待挂起的模拟器也不误报完成."""
        model = FakeMessagesListChatModel(
            responses=[
                AIMessage(
                    "已经走到了",
                    tool_calls=[
                        {
                            "name": "submit_action",
                            "args": {
                                "skill": "move_relative",
                                "arguments": {"distance_m": 0.1},
                            },
                            "id": "move",
                        }
                    ],
                )
            ]
        )
        with patch.object(FakeMessagesListChatModel, "bind_tools", return_value=model):
            reply = await self._agent(model).decide(timeout_s=1)
        self.assertEqual(reply.text, "")
        self.assertIsNotNone(reply.pending_action_id)
        actions = self.runtime.list_actions(self.task_id)
        self.assertEqual(len(actions), 1)
        self.assertNotEqual(actions[0].status, ActionStatus.SUCCEEDED)

    async def test_invalid_batch_has_no_side_effect(self) -> None:
        """动作混用工具、未知参数和模型伪造答案均在执行前被拒绝."""
        calls = [
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "submit_action",
                        "args": {
                            "skill": "move_relative",
                            "arguments": {"distance_m": 0.1},
                        },
                        "id": "1",
                    },
                    {"name": "get_teaching_state", "args": {}, "id": "2"},
                ],
            ),
            AIMessage(
                "",
                tool_calls=[
                    {"name": "get_teaching_state", "args": {"task_id": 999}, "id": "3"}
                ],
            ),
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "evaluate_answer",
                        "args": {
                            "question_id": "invented",
                            "answer": "圆形",
                            "submission_id": "invented",
                        },
                        "id": "4",
                    }
                ],
            ),
            AIMessage(
                "",
                tool_calls=[
                    {"name": "cancel_action", "args": {"action_id": True}, "id": "5"}
                ],
            ),
        ]
        for response in calls:
            model = FakeMessagesListChatModel(responses=[response])
            with patch.object(
                FakeMessagesListChatModel, "bind_tools", return_value=model
            ):
                with self.assertRaises(ValueError):
                    _ = await self._agent(model).decide()
        self.assertEqual(self.runtime.list_actions(self.task_id), [])

    async def test_cancel_during_inference_discards_late_reply(self) -> None:
        """任务取消不等待模型，迟到的回答被宿主丢弃."""
        model = FakeMessagesListChatModel(responses=[AIMessage("unused")])

        async def cancel_then_reply(*_args: object, **_kwargs: object) -> AIMessage:
            _ = await self.gateway.cancel_task(self.task_id)
            return AIMessage("迟到的回答")

        with (
            patch.object(FakeMessagesListChatModel, "bind_tools", return_value=model),
            patch.object(
                FakeMessagesListChatModel, "ainvoke", side_effect=cancel_then_reply
            ),
            self.assertRaises(RuntimeError),
        ):
            _ = await self._agent(model).decide()
