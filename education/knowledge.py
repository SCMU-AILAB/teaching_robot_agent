# education/knowledge.py
"""首版人工编写的离线教学内容，不假装来自现场观察."""

from domain.education import KnowledgePoint, Lesson, LessonStep


def build_shapes_lesson() -> Lesson:
    """创建平面形状示例，物体截面与整体形状明确区分."""
    source = "项目内人工编写示例：education/knowledge.py；正式教学前由负责人审核"
    return Lesson(
        "tabletop_shapes",
        "认识桌面物体的平面形状",
        "区分圆形和长方形，并说明观察的是物体表面或轮廓",
        (
            LessonStep(
                KnowledgePoint(
                    "circle",
                    "圆形",
                    "圆形边界上的点到圆心距离相等。这里假设杯口轮廓为圆形，"
                    + "讨论的是杯口的平面轮廓，不是说整个杯子是平面圆形。",
                    source,
                ),
                "示例中的杯口轮廓是什么平面形状？请回答：圆形或长方形。",
                ("圆形", "圆"),
                ("长方形", "矩形"),
                "想一想杯口轮廓有没有四条直边。",
            ),
            LessonStep(
                KnowledgePoint(
                    "rectangle",
                    "长方形",
                    "长方形有四条边和四个直角。这里假设书本封面的轮廓为长方形，"
                    + "讨论的是封面，不是整本书的立体形状。",
                    source,
                ),
                "示例中的书本封面轮廓是什么平面形状？请回答：圆形或长方形。",
                ("长方形", "矩形"),
                ("圆形", "圆"),
                "回忆具有四个直角的平面图形。",
            ),
        ),
    )
