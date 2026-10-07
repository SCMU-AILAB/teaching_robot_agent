# app/robot_audio_demo.py
"""运行完整模拟输入、Agent 决策及 Runtime 播报闭环，不依赖远端服务."""

import asyncio
import sys
from collections.abc import Sequence
from typing import override

from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from app.audio import build_simulated_audio
from app.robot_application import RobotApplication
from domain.models import ActionStatus
from perception.simulated import SimulatedPerception
from providers.local_model import build_local_model
from robot.simulated import SimulatedAdapter
from storage.evidence import EvidenceStore


class ScriptedDemoModel(FakeMessagesListChatModel):
    """仅用于离线演示的受控模型，工具协议由真实 Agent 处理."""

    @override
    def bind_tools(
        self,
        tools: Sequence[object],
        *,
        tool_choice: str | None = None,
        **kwargs: object,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        """接受工具登记并返回预先提供的模型消息."""
        return self


async def run_demo(*, local_model: bool = False) -> None:
    """默认受控模型；显式选择本地模型时沿用 AGENT 环境配置."""
    # ========== Step1: 装配单个 Agent 和同租约的模拟语音依赖 ==========
    evidence = EvidenceStore()
    if local_model:
        model = build_local_model("AGENT")
    else:
        # 演示用固定回答，真实 LangChain 工具调用由集成测试及本地模型模式验证。
        model = ScriptedDemoModel(
            responses=[AIMessage("热水壶可能烫伤，请让成年人帮助取热水。")]
        )
    app = RobotApplication(
        SimulatedAdapter(),
        SimulatedPerception(evidence),
        model,
        _audio=build_simulated_audio(evidence, "热水壶能碰吗？"),
    )
    try:
        await app.start()
        task = await app.create_task("家庭安全问答")
        # ========== Step2: 最终转写统一入队，宿主调用模型并等待实际播报终态 ==========
        await app.start_recording(task.task_id, "demo-input")
        item = await app.finish_recording("demo-input")
        assert item is not None
        print(f"模拟语音输入：{item.text}")
        reply = await app.process_next()
        print(f"机器人回答：{reply.text}")
        records = app.runtime.list_actions(task.task_id)
        if not records or any(
            record.status != ActionStatus.SUCCEEDED for record in records
        ):
            raise RuntimeError("Demo actions did not succeed")
        for record in records:
            print(
                f"动作 {record.action_id}：{record.raw_request.skill_name} / {record.status.value}"
            )
        print("语音及设备均为模拟；没有真实声音输出，回答不会自动验收任务。")
        state = await app.cancel_task(task.task_id)
        print(f"统一停止结果：{state.status.value}")
    finally:
        await app.close()


if __name__ == "__main__":
    if set(sys.argv[1:]) - {"--local-model"}:
        raise SystemExit("用法：uv run python -m app.robot_audio_demo [--local-model]")
    asyncio.run(run_demo(local_model="--local-model" in sys.argv[1:]))
