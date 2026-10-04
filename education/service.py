# education/service.py
"""独立于模型、运动和语音的教学状态服务."""

import logging
from dataclasses import replace
from uuid import uuid4

from domain.education import (
    AnswerEvaluation,
    AnswerVerdict,
    KnowledgePoint,
    Lesson,
    TeachingQuestion,
    TeachingSession,
    TeachingStage,
)
from domain.validation import nonempty_string, positive_int
from education.evaluator import AnswerEvaluator, normalize_answer

logger = logging.getLogger(__name__)


class EducationService:
    """单进程同步管理教学状态，出题与推进均为显式操作."""

    def __init__(
        self,
        _lesson: Lesson,
        _evaluator: AnswerEvaluator | None = None,
    ) -> None:
        """验证主题并保存依赖，禁止含混答案规则进入教学流程."""
        _ = nonempty_string(_lesson.topic_id, "topic_id")
        _ = nonempty_string(_lesson.goal, "goal")
        if not _lesson.steps:
            raise ValueError("Lesson must contain at least one step")
        ids: set[str] = set()
        for step in _lesson.steps:
            identifier = nonempty_string(step.knowledge.knowledge_id, "knowledge_id")
            if identifier in ids:
                raise ValueError("Duplicate knowledge_id")
            ids.add(identifier)
            for value in (
                step.knowledge.title,
                step.knowledge.explanation,
                step.knowledge.source,
                step.question,
                step.hint,
            ):
                _ = nonempty_string(value, "lesson text")
            accepted = {normalize_answer(value) for value in step.accepted_answers}
            rejected = {normalize_answer(value) for value in step.incorrect_answers}
            if not accepted or "" in accepted | rejected or accepted & rejected:
                raise ValueError("Answer rules must be nonempty and unambiguous")
        self._lesson: Lesson = _lesson
        self._evaluator: AnswerEvaluator = _evaluator or AnswerEvaluator()
        self._sessions: dict[int, TeachingSession] = {}

    def start_session(self, task_id: int) -> TeachingSession:
        """创建教学会话，重复调用返回已有进度而不是覆盖它."""
        _ = positive_int(task_id, "task_id")
        if task_id not in self._sessions:
            self._sessions[task_id] = TeachingSession(
                task_id,
                self._lesson.topic_id,
                self._lesson.goal,
                TeachingStage.EXPLAINING,
                0,
                len(self._lesson.steps),
            )
        return self._sessions[task_id]

    def get_state(self, task_id: int) -> TeachingSession:
        """获取不可变教学快照，未知任务抛出 KeyError."""
        return self._sessions[task_id]

    def lookup_knowledge(self, query: str) -> tuple[KnowledgePoint, ...]:
        """按知识标题或说明匹配，返回附人工来源的知识点."""
        query = nonempty_string(query, "query").strip().casefold()
        return tuple(
            step.knowledge
            for step in self._lesson.steps
            if query in step.knowledge.title.casefold()
            or query in step.knowledge.explanation.casefold()
        )

    def get_explanation(self, task_id: int) -> KnowledgePoint:
        """读取当前讲解依据，不推进状态，方便处理追问或重复讲解."""
        state = self.get_state(task_id)
        return self._lesson.steps[state.step_index].knowledge

    def ask_question(self, task_id: int) -> TeachingQuestion:
        """讲解后出题或反馈后重试，等待期间重复调用返回相同问题."""
        state = self.get_state(task_id)
        if state.stage == TeachingStage.AWAITING_ANSWER:
            assert state.pending_question is not None
            return state.pending_question
        if state.stage == TeachingStage.COMPLETED:
            raise ValueError("Teaching is already completed")
        if (
            state.stage == TeachingStage.FEEDBACK
            and state.evaluations[-1].verdict == AnswerVerdict.CORRECT
        ):
            raise ValueError("Advance after a correct answer before asking again")
        step = self._lesson.steps[state.step_index]
        question = TeachingQuestion(
            uuid4().hex, step.question, step.knowledge.knowledge_id
        )
        self._sessions[task_id] = replace(
            state, stage=TeachingStage.AWAITING_ANSWER, pending_question=question
        )
        return question

    def evaluate_answer(
        self, task_id: int, question_id: str, answer: str, submission_id: str
    ) -> AnswerEvaluation:
        """评价当前回答，按提交编号去重并拒绝过期问题.

        Args:
            task_id: 已创建教学会话的任务编号。
            question_id: 出题时返回的唯一问题编号。
            answer: 学生原始回答。
            submission_id: 重试请求保持不变的提交编号。

        Returns:
            已保存的评价，相同提交重试返回原结果。

        Raises:
            KeyError: 教学会话不存在。
            ValueError: 编号冲突、问题过期或当前阶段不能接受回答。
        """
        # ========== Step1: 检查重试与当前问题 ==========
        state = self.get_state(task_id)
        for previous in state.evaluations:
            if previous.submission_id == submission_id:
                if previous.question_id != question_id or previous.answer != answer:
                    raise ValueError("Submission ID reused with different content")
                return previous
        question = state.pending_question
        if (
            state.stage != TeachingStage.AWAITING_ANSWER
            or question is None
            or question.question_id != question_id
        ):
            raise ValueError("Answer does not match the pending question")
        # ========== Step2: 评价并原子保存反馈阶段 ==========
        evaluation = self._evaluator.evaluate(
            self._lesson.steps[state.step_index], question, answer, submission_id
        )
        self._sessions[task_id] = replace(
            state,
            stage=TeachingStage.FEEDBACK,
            pending_question=None,
            evaluations=(*state.evaluations, evaluation),
        )
        logger.info(
            "[EducationService.evaluate_answer] 保存评价 task_id=%s verdict=%s",
            task_id,
            evaluation.verdict.value,
        )
        return evaluation

    def advance(self, task_id: int) -> TeachingSession:
        """正确回答后显式推进一步，最后一个知识点完成后结束教学."""
        state = self.get_state(task_id)
        if (
            state.stage != TeachingStage.FEEDBACK
            or state.evaluations[-1].verdict != AnswerVerdict.CORRECT
        ):
            raise ValueError("Only a correct evaluated answer can advance teaching")
        mastered = (
            *state.mastered_knowledge_ids,
            self._lesson.steps[state.step_index].knowledge.knowledge_id,
        )
        completed = state.step_index + 1 == state.total_steps
        updated = replace(
            state,
            stage=TeachingStage.COMPLETED if completed else TeachingStage.EXPLAINING,
            step_index=state.step_index if completed else state.step_index + 1,
            mastered_knowledge_ids=mastered,
        )
        self._sessions[task_id] = updated
        return updated
