# tests/test_classroom_host.py
"""验证课堂宿主对教学、模型、动作与取消的协调."""

import asyncio
import unittest
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import override

from agent.classroom_host import ClassroomHost, DecisionAgent
from agent.embodied_agent import AgentTurn
from app.team_gateway import TeamGateway, UserInput
from domain.education import TeachingStage
from domain.models import ActionStatus, TaskStatus
from domain.services import ObservationResult
from education.knowledge import build_shapes_lesson
from education.service import EducationService
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter, SimulationMode
from runtime.action_manager import ActionManager
from skills.move_relative import MoveRelativeSkill
from skills.registry import SkillRegistry
from storage.evidence import EvidenceStore


class ScriptedAgent(DecisionAgent):
    """用可控异步响应替代远端模型，保留真实执行层."""

    def __init__(
        self, _handler: Callable[[UserInput | None], Awaitable[AgentTurn]]
    ) -> None:
        """注入本测试的决策步骤."""
        self.handler: Callable[[UserInput | None], Awaitable[AgentTurn]] = _handler
        self.calls: int = 0

    @override
    async def decide(
        self,
        user_input: UserInput | None = None,
        observation: ObservationResult | None = None,
        timeout_s: float = 90,
    ) -> AgentTurn:
        """记录调用次数并执行测试脚本."""
        self.calls += 1
        return await self.handler(user_input)


class ClassroomHostTests(unittest.IsolatedAsyncioTestCase):
    """课堂结束与停止必须由真实状态支持，输入重试不能重复副作用."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """初始化依赖，设备连接留到测试生命周期."""
        super().__init__(_method_name)
        self.robot: SimulatedAdapter = SimulatedAdapter()
        self.runtime: ActionManager = ActionManager(
            self.robot, SkillRegistry([MoveRelativeSkill()])
        )
        self.gateway: TeamGateway = TeamGateway(
            self.runtime, SimulatedPerception(EvidenceStore())
        )
        self.education: EducationService = EducationService(build_shapes_lesson())
        self.agent: ScriptedAgent = ScriptedAgent(self._answer)
        self.host: ClassroomHost = ClassroomHost(
            self.gateway, self.education, self._factory
        )
        self.task_id: int = self.gateway.create_task("教学").task_id

    def _factory(self, _task_id: int) -> DecisionAgent:
        """返回当前测试的决策器."""
        return self.agent

    async def _answer(self, _item: UserInput | None) -> AgentTurn:
        """默认追问只返回讲解."""
        return AgentTurn("讨论的是杯口的平面轮廓。")

    @override
    async def asyncSetUp(self) -> None:
        """启动模拟设备并开始课堂."""
        await self.runtime.start()
        _ = await self.host.start(self.task_id)

    @override
    async def asyncTearDown(self) -> None:
        """即使断言失败也结束课堂与模拟设备."""
        await self.host.close()
        await self.runtime.close()

    async def test_complete_without_fake_actions_and_replay(self) -> None:
        """两题答对后纯教学任务结束，最终输入重试不重复评分."""
        for answer in ("圆形", "长方形"):
            state = self.education.get_state(self.task_id)
            assert state.pending_question is not None
            item = self.gateway.submit_text(
                self.task_id, answer, state.pending_question.question_id
            )
            reply = await self.host.process_next()
            self.assertEqual(await self.host.handle(item), reply)
        self.assertEqual(reply.task_status, TaskStatus.COMPLETED)
        self.assertEqual(reply.teaching.stage, TeachingStage.COMPLETED)
        self.assertEqual(self.runtime.list_actions(), [])
        self.assertEqual(self.agent.calls, 0)
        with self.assertRaises(ValueError):
            _ = await self.host.handle(replace(item, text="不同内容"))

    async def test_followup_preserves_question_and_deduplicates(self) -> None:
        """自由追问走模型，问题编号与教学进度不变."""
        before = self.education.get_state(self.task_id)
        item = self.gateway.submit_text(self.task_id, "为什么不能说整个杯子是圆形？")
        reply = await self.host.process_next()
        self.assertEqual(reply.teaching, before)
        self.assertEqual(await self.host.handle(item), reply)
        self.assertEqual(self.agent.calls, 1)

    async def test_action_terminal_resumes_model_once(self) -> None:
        """只有实际动作成功后续接模型，同一输入不会再次移动."""

        async def move_then_explain(item: UserInput | None) -> AgentTurn:
            if item is not None:
                record = await self.gateway.submit_action(
                    self.task_id, "move_relative", {"distance_m": 0.01}
                )
                return AgentTurn("错误的提前完成声明", record.action_id)
            self.assertEqual(
                self.runtime.list_actions()[0].status, ActionStatus.SUCCEEDED
            )
            return AgentTurn("模拟动作已验证完成。")

        self.agent.handler = move_then_explain
        item = self.gateway.submit_text(self.task_id, "请演示相对移动")
        reply = await self.host.process_next()
        self.assertEqual(reply.text, "模拟动作已验证完成。")
        self.assertEqual(len(reply.action_ids), 1)
        self.assertEqual(await self.host.handle(item), reply)
        self.assertEqual(self.agent.calls, 2)
        self.assertEqual(len(self.runtime.list_actions()), 1)

    async def test_action_failure_does_not_resume_or_retry(self) -> None:
        """设备失败直接报告真实状态并结束课堂，不让模型自动重试."""
        self.robot.mode = SimulationMode.FAILURE

        async def move(_item: UserInput | None) -> AgentTurn:
            action = await self.gateway.submit_action(
                self.task_id, "move_relative", {"distance_m": 0.01}
            )
            return AgentTurn("", action.action_id)

        self.agent.handler = move
        _ = self.gateway.submit_text(self.task_id, "移动")
        reply = await self.host.process_next()
        self.assertEqual(reply.task_status, TaskStatus.FAILED)
        self.assertIn("failed", reply.text)
        self.assertEqual(self.agent.calls, 1)

    async def test_stop_during_model_or_motion(self) -> None:
        """停止不等待消费锁，挂起模型或动作均可取消且不能重放输入."""
        entered = asyncio.Event()

        async def hanging(_item: UserInput | None) -> AgentTurn:
            self.robot.mode = SimulationMode.HANG
            action = await self.gateway.submit_action(
                self.task_id, "move_relative", {"distance_m": 0.1}
            )
            entered.set()
            _ = await asyncio.Event().wait()
            return AgentTurn("", action.action_id)

        self.agent.handler = hanging
        item = self.gateway.submit_text(self.task_id, "移动")
        worker = asyncio.create_task(self.host.process_next())
        _ = await entered.wait()
        async with asyncio.timeout(1):
            stopped = await self.host.cancel(self.task_id)
            with self.assertRaises(asyncio.CancelledError):
                _ = await worker
        self.assertEqual(stopped.status, TaskStatus.CANCELLED)
        self.assertFalse((await self.robot.get_state()).is_moving)
        self.assertTrue(self.runtime.list_actions()[0].status.is_terminal)
        with self.assertRaises(ValueError):
            _ = await self.host.handle(item)

    async def test_incomplete_teaching_cannot_complete_task(self) -> None:
        """未完成教学快照不能使任务成功结束."""
        state = self.education.get_state(self.task_id)
        with self.assertRaises(ValueError):
            _ = self.gateway.complete_teaching_task(state)

    async def test_completed_lesson_does_not_hide_running_action(self) -> None:
        """即使教学已经完成，执行层仍拒绝把未结束动作算作成功."""
        for answer in ("圆形", "长方形"):
            question = self.education.ask_question(self.task_id)
            _ = self.education.evaluate_answer(
                self.task_id, question.question_id, answer, question.question_id
            )
            _ = self.education.advance(self.task_id)
        self.robot.mode = SimulationMode.HANG
        _ = await self.gateway.submit_action(
            self.task_id, "move_relative", {"distance_m": 0.1}
        )
        with self.assertRaises(ValueError):
            _ = self.gateway.complete_teaching_task(
                self.education.get_state(self.task_id)
            )
        self.assertEqual(
            (await self.gateway.get_snapshot(self.task_id)).task.status,
            TaskStatus.RUNNING,
        )

    async def test_continuous_actions_are_bounded(self) -> None:
        """模型持续要求动作时，宿主到达上限后不再请求下一次决策."""

        async def move(_item: UserInput | None) -> AgentTurn:
            action = await self.gateway.submit_action(
                self.task_id, "move_relative", {"distance_m": 0.01}
            )
            return AgentTurn("", action.action_id)

        self.agent.handler = move
        _ = self.gateway.submit_text(self.task_id, "连续移动")
        reply = await self.host.process_next()
        self.assertEqual(len(reply.action_ids), 3)
        self.assertEqual(self.agent.calls, 3)
        self.assertIn("上限", reply.text)
