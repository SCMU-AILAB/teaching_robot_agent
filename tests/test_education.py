# tests/test_education.py
"""验证教学推进、回答去重、过期拒绝与规则评价边界."""

import unittest
from dataclasses import FrozenInstanceError, replace

from domain.education import AnswerVerdict, TeachingStage
from education.knowledge import build_shapes_lesson
from education.service import EducationService


def attempt_mutation(value: object, attribute: str) -> None:
    """模拟不受静态类型约束的外部调用方修改快照."""
    setattr(value, attribute, 99)


class EducationTests(unittest.TestCase):
    """教学逻辑离线运行，不依赖模型、摄像头或设备."""

    def test_lesson_completes_only_after_correct_answers(self) -> None:
        """两道题分别正确并显式推进后才完成主题."""
        service = EducationService(build_shapes_lesson())
        _ = service.start_session(1)
        for index, answer in enumerate(("圆形。", "矩形")):
            question = service.ask_question(1)
            evaluation = service.evaluate_answer(
                1, question.question_id, answer, str(index)
            )
            self.assertEqual(evaluation.verdict, AnswerVerdict.CORRECT)
            self.assertTrue(evaluation.source)
            self.assertEqual(service.get_state(1).stage, TeachingStage.FEEDBACK)
            _ = service.advance(1)
        state = service.get_state(1)
        self.assertEqual(state.stage, TeachingStage.COMPLETED)
        self.assertEqual(state.mastered_knowledge_ids, ("circle", "rectangle"))
        with self.assertRaises(ValueError):
            _ = service.ask_question(1)
        with self.assertRaises(ValueError):
            _ = service.advance(1)

    def test_wrong_answer_retries_with_new_question_id(self) -> None:
        """错误答案保留进度，新问题拒绝旧问题的迟到回答."""
        service = EducationService(build_shapes_lesson())
        _ = service.start_session(1)
        old = service.ask_question(1)
        result = service.evaluate_answer(1, old.question_id, "长方形", "one")
        self.assertEqual(result.verdict, AnswerVerdict.INCORRECT)
        with self.assertRaises(ValueError):
            _ = service.advance(1)
        current = service.ask_question(1)
        self.assertNotEqual(current.question_id, old.question_id)
        with self.assertRaises(ValueError):
            _ = service.evaluate_answer(1, old.question_id, "圆形", "late")
        self.assertEqual(service.get_state(1).step_index, 0)

    def test_free_text_is_not_keyword_matched(self) -> None:
        """否定、猜测和多选表达不会因包含正确关键词而通过."""
        for answer in ("不是圆形", "圆形或者长方形", "也许是圆形", "不知道"):
            with self.subTest(answer=answer):
                service = EducationService(build_shapes_lesson())
                _ = service.start_session(1)
                question = service.ask_question(1)
                result = service.evaluate_answer(1, question.question_id, answer, "one")
                self.assertEqual(result.verdict, AnswerVerdict.NEEDS_REVIEW)
                with self.assertRaises(ValueError):
                    _ = service.advance(1)

    def test_submission_is_idempotent_and_conflicts_are_rejected(self) -> None:
        """重复提交不增加评价记录，复用编号修改内容时拒绝."""
        service = EducationService(build_shapes_lesson())
        _ = service.start_session(1)
        question = service.ask_question(1)
        original = service.evaluate_answer(1, question.question_id, "圆形", "one")
        _ = service.advance(1)
        self.assertEqual(
            service.evaluate_answer(1, question.question_id, "圆形", "one"), original
        )
        with self.assertRaises(ValueError):
            _ = service.evaluate_answer(1, question.question_id, "长方形", "one")
        self.assertEqual(len(service.get_state(1).evaluations), 1)

    def test_read_and_repeated_start_preserve_progress(self) -> None:
        """重复读取讲解和创建会话不重置待回答问题，任务相互隔离."""
        service = EducationService(build_shapes_lesson())
        _ = service.start_session(1)
        question = service.ask_question(1)
        snapshot = service.get_state(1)
        _ = service.get_explanation(1)
        self.assertEqual(service.start_session(1), snapshot)
        self.assertEqual(service.ask_question(1), question)
        other = service.start_session(2)
        self.assertEqual(other.stage, TeachingStage.EXPLAINING)
        with self.assertRaises(FrozenInstanceError):
            attempt_mutation(snapshot, "step_index")

    def test_invalid_rules_and_empty_answers_do_not_modify_state(self) -> None:
        """拒绝含混规则和空回答，失败时保留待回答状态."""
        lesson = build_shapes_lesson()
        step = replace(lesson.steps[0], incorrect_answers=("圆形",))
        with self.assertRaises(ValueError):
            _ = EducationService(replace(lesson, steps=(step,)))
        service = EducationService(lesson)
        _ = service.start_session(1)
        question = service.ask_question(1)
        before = service.get_state(1)
        with self.assertRaises(ValueError):
            _ = service.evaluate_answer(1, question.question_id, "  ", "one")
        self.assertEqual(service.get_state(1), before)
