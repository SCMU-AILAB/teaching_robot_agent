# app/teaching_demo.py
"""统一输入驱动的教学演示：python -m app.teaching_demo."""

import asyncio

from agent.teaching_flow import TeachingFlow
from app.team_gateway import TeamGateway
from domain.services import TranscriptResult
from education.knowledge import build_shapes_lesson
from education.service import EducationService
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.registry import SkillRegistry
from storage.evidence import EvidenceStore


async def run_app() -> None:
    """通过文字和模拟语音输入完成两道题，不调用模型."""
    perception = SimulatedPerception(EvidenceStore())
    try:
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(runtime, perception)
            flow = TeachingFlow(gateway, EducationService(build_shapes_lesson()))
            task = gateway.create_task("认识桌面物体的平面形状")
            reply = await flow.start(task.task_id)
            print(reply.text)
            for index, answer in enumerate(("长方形", "圆形", "长方形")):
                question = reply.state.pending_question
                assert question is not None
                if index == 2:
                    _ = gateway.submit_transcript(
                        task.task_id,
                        "demo-recording",
                        TranscriptResult(answer),
                        question_id=question.question_id,
                    )
                else:
                    _ = gateway.submit_text(task.task_id, answer, question.question_id)
                reply = await flow.process_next()
                print(reply.text)
            print("教学状态：", reply.state.stage.value)
            print("已掌握：", reply.state.mastered_knowledge_ids)
    finally:
        await perception.close()


if __name__ == "__main__":
    asyncio.run(run_app())
