# domain/education.py
"""教学状态与规则评价的不可变数据契约."""

from dataclasses import dataclass
from enum import StrEnum


class TeachingStage(StrEnum):
    """教学进度独立于机器人动作状态."""

    EXPLAINING = "explaining"
    AWAITING_ANSWER = "awaiting_answer"
    FEEDBACK = "feedback"
    COMPLETED = "completed"


class AnswerVerdict(StrEnum):
    """区分已知答案与规则无法评价的自由表达."""

    CORRECT = "correct"
    INCORRECT = "incorrect"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class KnowledgePoint:
    """教学知识及可追溯出处，示例资料须明确标注人工编写."""

    knowledge_id: str
    title: str
    explanation: str
    source: str


@dataclass(frozen=True)
class LessonStep:
    """一个知识点与一道有明确可接受答案的问题."""

    knowledge: KnowledgePoint
    question: str
    accepted_answers: tuple[str, ...]
    incorrect_answers: tuple[str, ...]
    hint: str


@dataclass(frozen=True)
class Lesson:
    """由连续教学步骤构成的主题."""

    topic_id: str
    title: str
    goal: str
    steps: tuple[LessonStep, ...]


@dataclass(frozen=True)
class TeachingQuestion:
    """每次出题生成唯一编号，防止迟到回答串入新问题."""

    question_id: str
    text: str
    knowledge_id: str


@dataclass(frozen=True)
class AnswerEvaluation:
    """保存原始回答、判定规则及所依据的知识来源."""

    submission_id: str
    question_id: str
    answer: str
    verdict: AnswerVerdict
    feedback: str
    knowledge_id: str
    source: str
    rule: str
    evaluated_at: float


@dataclass(frozen=True)
class TeachingSession:
    """任务对应的教学快照，读取者不能修改服务内状态."""

    task_id: int
    topic_id: str
    goal: str
    stage: TeachingStage
    step_index: int
    total_steps: int
    pending_question: TeachingQuestion | None = None
    evaluations: tuple[AnswerEvaluation, ...] = ()
    mastered_knowledge_ids: tuple[str, ...] = ()
