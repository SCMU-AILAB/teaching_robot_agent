# tests/test_scene_brief.py
"""观察摘要的物体方位、关联安全资料、截断与渲染测试."""

import unittest
from dataclasses import replace

from agent.scene_brief import build_scene_brief
from domain.education import KnowledgePoint
from domain.models import Pose2D
from domain.services import (
    FrameReference,
    ObjectObservation,
    ObservationResult,
    VisionAnalysis,
)
from education.knowledge_service import KnowledgeService, build_home_knowledge


def observation_with(*items: tuple[str, str]) -> ObservationResult:
    """按 (标签, 方位) 构造模拟观察结果."""
    objects = tuple(
        ObjectObservation(f"obj-{index}", label, (("position", position),))
        for index, (label, position) in enumerate(items)
    )
    return ObservationResult(
        "obs-1",
        1,
        "桌面有什么？",
        FrameReference("frame-1", "cam", 100.0, 640, 480, "ev-1", 1),
        VisionAnalysis("桌面上有几件物品", "sim", True, objects),
        101.0,
        False,
        False,
    )


def knowledge_with_long_explanations() -> KnowledgeService:
    """构造每条讲解为 300 字、均关联常见物体的知识服务."""
    labels = ("热水壶", "插座", "刀", "药瓶", "燃气灶")
    return KnowledgeService(
        tuple(
            KnowledgePoint(
                f"k{index}", f"主题{index}", "字" * 300, "测试来源", (), (label,)
            )
            for index, label in enumerate(labels)
        )
    )


class SceneBriefTests(unittest.TestCase):
    """验证观察结果与知识的组合."""

    def test_brief_links_objects_to_safety(self) -> None:
        """物体按顺序关联安全条目并渲染位姿、来源与模拟标记."""
        brief = build_scene_brief(
            observation_with(("热水壶", "左侧"), ("插座", "右侧")),
            Pose2D(1.0, 0.0, 0.5),
            build_home_knowledge(),
        )
        self.assertEqual(brief.objects, (("热水壶", "左侧"), ("插座", "右侧")))
        self.assertEqual(
            [i.knowledge_id for i in brief.related_safety], ["hot_water", "electricity"]
        )
        text = brief.to_prompt()
        for expected in (
            "热水壶（左侧）",
            "热水与烫伤预防",
            "来源：项目人工编写",
            "x=1.00",
            "模拟=True",
        ):
            self.assertIn(expected, text)

    def test_limits_and_truncation(self) -> None:
        """关联条目最多 3 条，讲解截断且不修改知识库原条目."""
        labels = [
            ("热水壶", "左侧"),
            ("插座", "中间"),
            ("刀", "右侧"),
            ("药瓶", "左侧"),
            ("燃气灶", "中间"),
        ]
        service = knowledge_with_long_explanations()
        brief = build_scene_brief(observation_with(*labels), None, service)
        self.assertEqual(len(brief.related_safety), 3)
        self.assertTrue(all(len(i.explanation) <= 201 for i in brief.related_safety))
        self.assertTrue(all(i.explanation.endswith("…") for i in brief.related_safety))
        self.assertEqual(len(service.lookup("主题0")[0].explanation), 300)

    def test_objects_limited_and_missing_position(self) -> None:
        """物体最多 8 个，缺少方位时标为不确定."""
        many = observation_with(*[(f"物体{i}", "左侧") for i in range(10)])
        self.assertEqual(
            len(build_scene_brief(many, None, build_home_knowledge()).objects), 8
        )
        bare = replace(
            many,
            analysis=replace(many.analysis, objects=(ObjectObservation("o", "绘本"),)),
        )
        self.assertEqual(
            build_scene_brief(bare, None, build_home_knowledge()).objects,
            (("绘本", "不确定"),),
        )

    def test_no_related_knowledge_note(self) -> None:
        """没有关联条目时明确提示，位姿未知时说明原因."""
        brief = build_scene_brief(
            observation_with(("绘本", "中间")), None, build_home_knowledge()
        )
        self.assertEqual(brief.note, "资料库没有相关条目")
        self.assertIn("相关安全资料：资料库没有相关条目", brief.to_prompt())
        self.assertIn("机器人位姿：未知（设备无定位能力）", brief.to_prompt())

    def test_no_objects_rendering(self) -> None:
        """没有物体时渲染固定提示而不报错."""
        brief = build_scene_brief(observation_with(), None, build_home_knowledge())
        self.assertIn("物体：未识别出明确物体", brief.to_prompt())

    def test_stale_and_simulated_pass_through(self) -> None:
        """过期标记从观察结果透传到提示文本."""
        obs = replace(observation_with(("热水壶", "左侧")), stale=True)
        brief = build_scene_brief(obs, None, build_home_knowledge())
        self.assertIn("过期=True", brief.to_prompt())
        self.assertIn("采集时间=100.0", brief.to_prompt())
