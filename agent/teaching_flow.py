# agent/teaching_flow.py
"""可替换为模型策略的离线教学流程，消费统一输入并保留问题归属."""

import asyncio
from dataclasses import dataclass

from agent.context import ContextBuilder, TeachingContext
from app.team_gateway import TeamGateway, UserInput
from domain.education import AnswerVerdict, TeachingSession, TeachingStage
from domain.models import TaskStatus
from education.service import EducationService


@dataclass(frozen=True)
class TeachingReply:
    """教学输出与状态，教学完成不冒充机器人任务已经结束."""

    text: str
    state: TeachingSession
    context: TeachingContext
    rejected: bool = False


class TeachingFlow:
    """串行处理教学输入，停止任务后禁止推进，重复输入返回原结果."""

    def __init__(self, _gateway: TeamGateway, _education: EducationService) -> None:
        """注入依赖，不创建后台消费者或连接设备."""
        self._gateway: TeamGateway = _gateway
        self._education: EducationService = _education
        self._context: ContextBuilder = ContextBuilder(_gateway, _education)
        self._lock: asyncio.Lock = asyncio.Lock()
        self._replies: dict[str, tuple[UserInput, TeachingReply]] = {}

    @staticmethod
    def _require_active(context: TeachingContext) -> None:
        """取消与终结任务不再生成新的教学行为."""
        if context.task.task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            raise ValueError("Task is no longer active")

    async def start(self, task_id: int) -> TeachingReply:
        """开始示例讲解并出题，重复调用不重置进度."""
        async with self._lock:
            snapshot = await self._gateway.get_snapshot(task_id)
            if snapshot.task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                raise ValueError("Task is no longer active")
            _ = self._education.start_session(task_id)
            context = await self._context.build(task_id)
            self._require_active(context)
            state = self._education.get_state(task_id)
            if state.stage == TeachingStage.COMPLETED:
                return TeachingReply("本主题教学已完成。", state, context)
            question = self._education.ask_question(task_id)
            return TeachingReply(
                "以下使用人工示例，不代表现场观察。\n"
                + self._education.get_explanation(task_id).explanation
                + "\n"
                + question.text,
                self._education.get_state(task_id),
                context,
            )

    async def process_next(self) -> TeachingReply:
        """从统一队列取出输入，只允许一个循环调用此消费入口."""
        return await self.handle(await self._gateway.next_input())

    async def handle(self, item: UserInput) -> TeachingReply:
        """处理追问或明确关联的问题答案，拒绝过期输入.

        Args:
            item: 从统一入口获得的文字或最终语音输入。

        Returns:
            教学文本、处理后的进度与本轮处理前上下文。

        Raises:
            KeyError: 教学会话或任务不存在。
            ValueError: 任务已停止，或输入编号被用于不同内容。
        """
        async with self._lock:
            # ========== Step1: 获取上下文并检查取消与重试 ==========
            context = await self._context.build(item.task_id, item)
            self._require_active(context)
            previous = self._replies.get(item.input_id)
            if previous is not None:
                if previous[0] != item:
                    raise ValueError("Input ID reused with different content")
                return previous[1]
            state = context.teaching
            rejected = False
            # ========== Step2: 根据显式问题编号区分追问与答案 ==========
            if state.stage == TeachingStage.COMPLETED:
                text = "本主题教学已完成。"
                rejected = True
            elif item.question_id is None:
                text = "当前是离线规则教学，暂不支持自由问答；可以重读当前知识点：\n"
                text += self._education.get_explanation(item.task_id).explanation
            elif (
                state.pending_question is None
                or item.question_id != state.pending_question.question_id
            ):
                text = "该回答对应的问题已经失效，请回答当前问题。"
                rejected = True
            else:
                result = self._education.evaluate_answer(
                    item.task_id, item.question_id, item.text, item.input_id
                )
                text = result.feedback
                if result.verdict == AnswerVerdict.CORRECT:
                    state = self._education.advance(item.task_id)
                    if state.stage == TeachingStage.COMPLETED:
                        text += "\n本主题教学已完成。"
                    else:
                        text += (
                            "\n"
                            + self._education.get_explanation(item.task_id).explanation
                        )
                if (
                    self._education.get_state(item.task_id).stage
                    != TeachingStage.COMPLETED
                ):
                    text += "\n" + self._education.ask_question(item.task_id).text
            # ========== Step3: 保存结果，重复消费不再次推进教学 ==========
            reply = TeachingReply(
                text, self._education.get_state(item.task_id), context, rejected
            )
            self._replies[item.input_id] = (item, reply)
            return reply
