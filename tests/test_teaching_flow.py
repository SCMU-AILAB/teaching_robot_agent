# tests/test_teaching_flow.py
"""验证统一输入队列驱动的教学流程与取消边界."""

import unittest

from agent.teaching_flow import TeachingFlow
from app.team_gateway import TeamGateway
from domain.education import TeachingStage
from domain.services import TranscriptResult
from education.knowledge import build_shapes_lesson
from education.service import EducationService
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.registry import SkillRegistry
from storage.evidence import EvidenceStore


class TeachingFlowTests(unittest.IsolatedAsyncioTestCase):
    """使用真实核心入口和模拟设备验证整条教学输入链路."""

    async def test_text_and_speech_complete_lesson(self) -> None:
        """文字与语音共享出题归属，答错重试后完成教学."""
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(runtime, SimulatedPerception(EvidenceStore()))
            education = EducationService(build_shapes_lesson())
            flow = TeachingFlow(gateway, education)
            task = gateway.create_task("教学")
            reply = await flow.start(task.task_id)
            for index, answer in enumerate(("长方形", "圆形", "矩形")):
                question = reply.state.pending_question
                assert question is not None
                _ = gateway.submit_transcript(
                    task.task_id,
                    str(index),
                    TranscriptResult(answer),
                    question.question_id,
                )
                reply = await flow.process_next()
            self.assertEqual(reply.state.stage, TeachingStage.COMPLETED)
            self.assertEqual(len(reply.state.evaluations), 3)
            self.assertEqual(
                reply.state.mastered_knowledge_ids, ("circle", "rectangle")
            )

    async def test_followup_and_stale_answer_preserve_pending_question(self) -> None:
        """追问不推进教学，过期回答不应用到下一题."""
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(runtime, SimulatedPerception(EvidenceStore()))
            flow = TeachingFlow(gateway, EducationService(build_shapes_lesson()))
            task = gateway.create_task("教学")
            initial = await flow.start(task.task_id)
            question = initial.state.pending_question
            assert question is not None
            _ = gateway.submit_text(task.task_id, "为什么？")
            followup = await flow.process_next()
            self.assertEqual(initial.state, followup.state)
            _ = gateway.submit_text(task.task_id, "圆形", question.question_id)
            advanced = await flow.process_next()
            _ = gateway.submit_text(task.task_id, "圆形", question.question_id)
            stale = await flow.process_next()
            self.assertTrue(stale.rejected)
            self.assertEqual(stale.state, advanced.state)

    async def test_duplicate_and_cancel_do_not_advance(self) -> None:
        """输入重试不再次推进，任务取消后保留教学进度并拒绝处理."""
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(runtime, SimulatedPerception(EvidenceStore()))
            education = EducationService(build_shapes_lesson())
            flow = TeachingFlow(gateway, education)
            task = gateway.create_task("教学")
            initial = await flow.start(task.task_id)
            question = initial.state.pending_question
            assert question is not None
            item = gateway.submit_text(task.task_id, "圆形", question.question_id)
            result = await flow.process_next()
            self.assertEqual(await flow.handle(item), result)
            _ = await gateway.cancel_task(task.task_id)
            with self.assertRaises(ValueError):
                _ = await flow.handle(item)
            self.assertEqual(education.get_state(task.task_id), result.state)
