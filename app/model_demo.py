# app/model_demo.py
"""本地模型联调：vision 图片路径 问题，或 agent 教学问题."""

import asyncio
import logging
import os
import sys
from pathlib import Path
from uuid import uuid4

from PIL import Image

from agent.context import ContextBuilder
from agent.embodied_agent import EmbodiedAgent
from agent.tools import ToolAdapter
from app.team_gateway import TeamGateway
from domain.services import FrameReference, VisionRequest
from education.knowledge import build_shapes_lesson
from education.service import EducationService
from perception.simulated import SimulatedPerception
from providers.local_model import build_local_model
from providers.ollama_vision import OllamaVisionProvider
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.registry import SkillRegistry
from storage.evidence import EvidenceStore


async def run_vision(image_path: str, question: str) -> None:
    """对本地静态照片运行真实视觉推理，不伪装成实时相机输入."""
    path = Path(image_path)
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Image file exceeds 20 MiB")
    with Image.open(path) as image:
        width, height = image.size
        if image.format not in {"PNG", "JPEG"}:
            raise ValueError("Only PNG and JPEG are supported")
        media_type = "image/png" if image.format == "PNG" else "image/jpeg"
    store = EvidenceStore()
    evidence = await store.save(path.read_bytes(), media_type, path.stat().st_mtime)
    frame = FrameReference(
        uuid4().hex,
        "static-file",
        evidence.captured_at,
        width,
        height,
        evidence.evidence_id,
        0,
    )
    model = build_local_model("VLM")
    provider = OllamaVisionProvider(model, model.model, store)
    result = await provider.analyze(VisionRequest(uuid4().hex, question, frame, 90))
    print(f"模型：{result.model_id}；输入：静态图片；推理：真实模型")
    print(result.summary)


async def run_agent(question: str) -> None:
    """真实模型查询教学工具；设备和感知为模拟实现，未注册移动技能."""
    perception = SimulatedPerception(EvidenceStore())
    try:
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(runtime, perception)
            education = EducationService(build_shapes_lesson())
            task = gateway.create_task("根据教学资料回答问题")
            _ = education.start_session(task.task_id)
            tools = ToolAdapter(task.task_id, gateway, education)
            agent = EmbodiedAgent(
                build_local_model("AGENT"),
                ContextBuilder(gateway, education),
                tools,
                task.task_id,
            )
            item = gateway.submit_text(task.task_id, question)
            reply = await agent.decide(item)
            print("模型：真实本地模型；设备与感知：模拟；移动技能：未注册")
            print(reply.text)
            if reply.pending_action_id is not None:
                print("等待动作终态：", reply.pending_action_id)
    finally:
        await perception.close()


async def run_app() -> None:
    """按命令行参数选择视觉或工具调用联调."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    args = sys.argv[1:]
    if len(args) == 3 and args[0] == "vision":
        await run_vision(args[1], args[2])
    elif len(args) == 2 and args[0] == "agent":
        await run_agent(args[1])
    else:
        raise SystemExit(
            "用法：python -m app.model_demo vision 图片路径 问题\n"
            + "或：python -m app.model_demo agent 教学问题"
        )


if __name__ == "__main__":
    asyncio.run(run_app())
