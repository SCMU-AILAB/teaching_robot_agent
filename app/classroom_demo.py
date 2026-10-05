# app/classroom_demo.py
"""真实本地模型处理追问、脚本学生完成课堂的装配示例."""

import asyncio
import logging
import os

from agent.classroom_host import ClassroomHost
from agent.context import ContextBuilder
from agent.embodied_agent import EmbodiedAgent
from agent.tools import ToolAdapter
from app.team_gateway import TeamGateway
from education.knowledge import build_shapes_lesson
from education.service import EducationService
from perception.simulated import SimulatedPerception
from providers.local_model import build_local_model
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.registry import SkillRegistry
from storage.evidence import EvidenceStore


async def run_app() -> None:
    """依次演示开始、真实模型追问、脚本作答和纯教学任务完成."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    model = build_local_model("AGENT")
    perception = SimulatedPerception(EvidenceStore())
    try:
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(runtime, perception)
            education = EducationService(build_shapes_lesson())

            def build_agent(task_id: int) -> EmbodiedAgent:
                """每个课堂绑定自己的工具与教学状态，共享模型客户端."""
                return EmbodiedAgent(
                    model,
                    ContextBuilder(gateway, education),
                    ToolAdapter(task_id, gateway, education),
                    task_id,
                )

            host = ClassroomHost(gateway, education, build_agent)
            try:
                task = gateway.create_task("认识圆形和长方形")
                reply = await host.start(task.task_id)
                print("演示：真实本地模型；模拟设备与感知；学生回答由脚本提供。")
                print(reply.text)
                _ = gateway.submit_text(
                    task.task_id, "请查询圆形资料，解释为什么说杯口轮廓而不是整个杯子？"
                )
                reply = await host.process_next()
                print(reply.text)
                for answer in ("圆形", "长方形"):
                    question = reply.teaching.pending_question
                    assert question is not None
                    _ = gateway.submit_text(task.task_id, answer, question.question_id)
                    reply = await host.process_next()
                    print(reply.text)
                print("教学状态：", reply.teaching.stage.value)
                print("任务状态：", reply.task_status.value)
            finally:
                await host.close()
    finally:
        await perception.close()


if __name__ == "__main__":
    asyncio.run(run_app())
