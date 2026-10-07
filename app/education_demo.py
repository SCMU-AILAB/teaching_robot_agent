# app/education_demo.py
"""离线教学流程演示：python -m app.education_demo."""

from education.knowledge import build_shapes_lesson
from education.service import EducationService


def run_app() -> None:
    """演示讲解、错误反馈、重新作答与教学完成."""
    service = EducationService(build_shapes_lesson())
    _ = service.start_session(1)
    print("示例教学，不代表真实摄像头观察。")
    print(service.get_explanation(1).explanation)
    question = service.ask_question(1)
    print(question.text)
    print(service.evaluate_answer(1, question.question_id, "长方形", "first").feedback)
    question = service.ask_question(1)
    print(service.evaluate_answer(1, question.question_id, "圆形", "retry").feedback)
    _ = service.advance(1)
    print(service.get_explanation(1).explanation)
    question = service.ask_question(1)
    print(question.text)
    print(service.evaluate_answer(1, question.question_id, "长方形", "second").feedback)
    print(service.advance(1))


if __name__ == "__main__":
    run_app()
