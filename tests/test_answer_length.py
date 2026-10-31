# tests/test_answer_length.py
"""回答篇幅控制：超长文字回答中断后按要求重新生成，工具调用不受篇幅要求影响."""

import json
import os
import unittest
from collections.abc import AsyncIterator, Sequence
from typing import override
from unittest.mock import patch

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from agent.embodied_agent import answer_instruction
from app.robot_application import RobotApplication
from perception.simulated import SimulatedPerception
from providers.local_model import answer_chars_from_env
from robot.simulated import SimulatedAdapter
from storage.evidence import EvidenceStore

LONG_ANSWER = "前面有一张小桌子和一把椅子，椅子是给人坐的，不能站上去，站上去容易摔倒受伤，要请大人帮忙。"
SHORT_ANSWER = "椅子不能站，容易摔倒。"


class StreamingScriptModel(BaseChatModel):
    """按顺序回放回复；流式调用逐字输出文字，再输出工具调用块."""

    responses: list[AIMessage]
    index: int = 0
    seen: list[list[BaseMessage]] = Field(default_factory=list)
    streamed: list[int] = Field(default_factory=list)

    @property
    @override
    def _llm_type(self) -> str:
        """模型类型标识."""
        return "streaming-script"

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        """记录本次消息并取出下一条回复."""
        self.seen.append(list(messages))
        reply = self.responses[self.index]
        self.index += 1
        return reply

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        """一次返回完整回复."""
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    @override
    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """逐字输出文字后输出工具调用，并记录调用方实际读取的字数."""
        reply = self._next(messages)
        self.streamed.append(0)
        for char in reply.text:
            self.streamed[-1] += 1
            yield ChatGenerationChunk(message=AIMessageChunk(content=char))
        for index, call in enumerate(reply.tool_calls):
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": call["name"],
                            "args": json.dumps(call["args"]),
                            "id": call["id"],
                            "index": index,
                        }
                    ],
                )
            )


def observe_after_preface() -> AIMessage:
    """模型先说一句再调用观察工具."""
    return AIMessage(
        "好的，小朋友，让我先仔细看看前面都有些什么东西吧。",
        tool_calls=[
            {
                "name": "observe_scene",
                "args": {"question": "前面有什么？"},
                "id": "o1",
            }
        ],
    )


class AnswerLengthTests(unittest.IsolatedAsyncioTestCase):
    """经完整主应用验证篇幅控制."""

    async def _ask(
        self, model: BaseChatModel, questions: Sequence[str], limit: int | None
    ) -> tuple[RobotApplication, list[str]]:
        """在同一任务中依次提问，返回应用与各轮回答."""
        app = RobotApplication(
            SimulatedAdapter(),
            SimulatedPerception(EvidenceStore()),
            model,
            _answer_max_chars=limit,
        )
        replies: list[str] = []
        try:
            await app.start()
            task = await app.create_task("篇幅")
            with patch.object(type(model), "bind_tools", return_value=model):
                for question in questions:
                    _ = app.gateway.submit_text(task.task_id, question)
                    replies.append((await app.process_next()).text)
        finally:
            await app.close()
        return app, replies

    async def test_long_answer_is_cut_and_regenerated(self) -> None:
        """超长回答读到上限 1.5 倍即停止，追加篇幅要求重新生成，记忆记录短回答."""
        model = StreamingScriptModel(
            responses=[AIMessage(LONG_ANSWER), AIMessage(SHORT_ANSWER)]
        )
        _, replies = await self._ask(model, ["椅子能站吗？"], 20)
        self.assertEqual(replies, [SHORT_ANSWER])
        self.assertEqual(model.streamed[0], 31)
        self.assertLess(model.streamed[0], len(LONG_ANSWER))
        last = model.seen[1][-1]
        self.assertEqual(last.text, answer_instruction(20))
        self.assertEqual(model.seen[1][:-1], model.seen[0])

    async def test_preface_before_tool_call_is_not_cut(self) -> None:
        """开场白超过字数上限、但未到中断长度时，工具调用照常执行；短回答直接采用."""
        model = StreamingScriptModel(
            responses=[observe_after_preface(), AIMessage(SHORT_ANSWER)]
        )
        _, replies = await self._ask(model, ["前面有什么？"], 20)
        self.assertEqual(replies, [SHORT_ANSWER])
        self.assertEqual(len(model.seen), 2)
        self.assertIsInstance(model.seen[1][-1], ToolMessage)

    async def test_non_streaming_model_is_limited_too(self) -> None:
        """不支持流式的模型一次返回超长回答，同样重新生成."""
        model = FakeMessagesListChatModel(
            responses=[AIMessage(LONG_ANSWER), AIMessage(SHORT_ANSWER)]
        )
        _, replies = await self._ask(model, ["椅子能站吗？"], 20)
        self.assertEqual(replies, [SHORT_ANSWER])

    async def test_no_limit_keeps_single_call(self) -> None:
        """未设上限时长回答原样采用."""
        model = StreamingScriptModel(responses=[AIMessage(LONG_ANSWER)])
        _, replies = await self._ask(model, ["椅子能站吗？"], None)
        self.assertEqual(replies, [LONG_ANSWER])
        self.assertEqual(model.streamed, [])

    def test_answer_chars_from_env(self) -> None:
        """默认 40，0 表示不限制，非法值报错."""
        with patch.dict(os.environ, {}, clear=False):
            _ = os.environ.pop("AGENT_ANSWER_CHARS", None)
            self.assertEqual(answer_chars_from_env(), 40)
        with patch.dict(os.environ, {"AGENT_ANSWER_CHARS": "0"}):
            self.assertIsNone(answer_chars_from_env())
        for raw in ("-1", "abc"):
            with patch.dict(os.environ, {"AGENT_ANSWER_CHARS": raw}):
                with self.assertRaises(ValueError):
                    _ = answer_chars_from_env()


if __name__ == "__main__":
    _ = unittest.main()
