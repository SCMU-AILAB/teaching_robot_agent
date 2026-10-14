# agent/scene_brief.py
"""观察结果与关联安全资料的紧凑摘要，供模型阅读."""

from dataclasses import dataclass, replace
from typing import override

from domain.education import KnowledgePoint
from domain.models import Pose2D
from domain.services import ObservationResult
from education.knowledge_service import KnowledgeService

MAX_OBJECTS = 8
MAX_RELATED = 3
MAX_EXPLANATION_CHARS = 200
UNKNOWN_POSITION = "不确定"
NO_KNOWLEDGE_NOTE = "资料库没有相关条目"


def _truncate(text: str, limit: int) -> str:
    """超长时取前 limit 字并追加省略号."""
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass(frozen=True)
class SceneBrief:
    """一次观察的摘要、物体方位、机器人位姿与关联安全资料."""

    observation: ObservationResult
    robot_pose: Pose2D | None
    related_safety: tuple[KnowledgePoint, ...]

    @property
    def objects(self) -> tuple[tuple[str, str], ...]:
        """返回最多 8 个 (标签, 方位)，缺少方位时为不确定."""
        return tuple(
            (item.label, dict(item.attributes).get("position", UNKNOWN_POSITION))
            for item in self.observation.analysis.objects[:MAX_OBJECTS]
        )

    @property
    def note(self) -> str:
        """没有关联资料时给出明确提示，避免模型自行编造."""
        return "" if self.related_safety else NO_KNOWLEDGE_NOTE

    def to_prompt(self) -> str:
        """按固定行顺序渲染为模型可读文本."""
        observation = self.observation
        objects = "、".join(
            f"{label}（{position}）" for label, position in self.objects
        )
        pose = self.robot_pose
        pose_text = (
            f"x={pose.x:.2f} y={pose.y:.2f} yaw={pose.yaw:.2f}"
            if pose is not None
            else "未知（设备无定位能力）"
        )
        lines = [
            f"观察摘要：{observation.analysis.summary}",
            f"物体：{objects or '未识别出明确物体'}",
            f"状态：模拟={observation.analysis.simulated} "
            + f"过期={observation.stale} "
            + f"采集时间={observation.frame.captured_at:.1f}",
            f"机器人位姿：{pose_text}",
        ]
        if self.related_safety:
            lines.append("相关安全资料：")
            lines.extend(
                f"- {item.title}：{item.explanation}（来源：{item.source}）"
                for item in self.related_safety
            )
        else:
            lines.append(f"相关安全资料：{self.note}")
        return "\n".join(lines)

    @override
    def __str__(self) -> str:
        """让仍使用 str(result) 的调用方得到可读摘要."""
        return self.to_prompt()


def build_scene_brief(
    observation: ObservationResult,
    robot_pose: Pose2D | None,
    knowledge: KnowledgeService,
) -> SceneBrief:
    """把观察物体关联到安全资料，返回截断后的副本而不改动知识库."""
    labels = [item.label for item in observation.analysis.objects[:MAX_OBJECTS]]
    related = tuple(
        replace(
            entry,
            explanation=_truncate(entry.explanation, MAX_EXPLANATION_CHARS),
        )
        for entry in knowledge.for_objects(labels)[:MAX_RELATED]
    )
    return SceneBrief(observation, robot_pose, related)
