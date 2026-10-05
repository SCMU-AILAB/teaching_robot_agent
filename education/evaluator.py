# education/evaluator.py
"""保守的精确答案评价，未知表达留待进一步确认."""

import time
import unicodedata

from domain.education import (
    AnswerEvaluation,
    AnswerVerdict,
    LessonStep,
    TeachingQuestion,
)
from domain.validation import nonempty_string


def normalize_answer(answer: str) -> str:
    """统一全半角和首尾标点，不删除否定词或内部语义."""
    return (
        unicodedata.normalize("NFKC", answer)
        .strip()
        .rstrip("。.!！?？")
        .strip()
        .casefold()
    )


class AnswerEvaluator:
    """仅对显式列出的答案下结论，避免关键词命中造成误判."""

    def evaluate(
        self,
        step: LessonStep,
        question: TeachingQuestion,
        answer: str,
        submission_id: str,
    ) -> AnswerEvaluation:
        """按规范化后的完整文本评价，并保存知识与规则依据.

        Args:
            step: 当前教学步骤。
            question: 当前待回答问题。
            answer: 学生原始回答。
            submission_id: 调用方为同一次提交保持不变的编号。

        Returns:
            正确、错误或待确认的评价记录。

        Raises:
            ValueError: 回答或提交编号为空。
        """
        _ = nonempty_string(answer, "answer")
        _ = nonempty_string(submission_id, "submission_id")
        normalized = normalize_answer(answer)
        if normalized in {normalize_answer(item) for item in step.accepted_answers}:
            verdict = AnswerVerdict.CORRECT
            feedback = "回答正确。" + step.knowledge.explanation
        elif normalized in {normalize_answer(item) for item in step.incorrect_answers}:
            verdict = AnswerVerdict.INCORRECT
            feedback = "再想一想。" + step.hint
        else:
            verdict = AnswerVerdict.NEEDS_REVIEW
            feedback = "这段表达暂时无法用当前规则判断，请按题目选项简短回答。"
        return AnswerEvaluation(
            submission_id,
            question.question_id,
            answer,
            verdict,
            feedback,
            step.knowledge.knowledge_id,
            step.knowledge.source,
            "normalized_exact_match_v1",
            time.time(),
        )
