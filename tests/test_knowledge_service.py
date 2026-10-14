# tests/test_knowledge_service.py
"""验证知识检索的打分排序与物体到安全主题的关联."""

import unittest
from typing import override

from education.knowledge_service import KnowledgeService, build_home_knowledge


class KnowledgeServiceTests(unittest.TestCase):
    """知识查询离线运行，不依赖模型或设备."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """声明每个用例使用的知识服务."""
        super().__init__(_method_name)
        self.service: KnowledgeService = build_home_knowledge()

    @override
    def setUp(self) -> None:
        """每个用例使用独立的开发用知识库."""
        self.service = build_home_knowledge()

    def test_natural_question_hits_hot_water(self) -> None:
        """自然问句应让热水条目排在首位."""
        ids = [item.knowledge_id for item in self.service.lookup("热水壶能碰吗？")]
        self.assertEqual(ids[0], "hot_water")

    def test_socket_follow_up(self) -> None:
        """省略主语的追问仍能命中用电条目."""
        self.assertEqual(self.service.lookup("那插座呢")[0].knowledge_id, "electricity")

    def test_ranking_is_stable(self) -> None:
        """相同查询得到相同顺序，且多主题问句命中全部相关条目."""
        first = self.service.lookup("厨房的刀和燃气灶")
        self.assertEqual(first, self.service.lookup("厨房的刀和燃气灶"))
        self.assertEqual({i.knowledge_id for i in first}, {"knife", "gas_stove"})

    def test_for_objects_links_labels(self) -> None:
        """物体标签按出现顺序关联到安全条目."""
        ids = [i.knowledge_id for i in self.service.for_objects(["电热水壶", "插线板"])]
        self.assertEqual(ids, ["hot_water", "electricity"])

    def test_for_objects_tolerates_modifiers(self) -> None:
        """带颜色或大小修饰词的标签仍可关联."""
        ids = [
            i.knowledge_id
            for i in self.service.for_objects(["白色电热水壶", "小水果刀"])
        ]
        self.assertEqual(ids, ["hot_water", "knife"])

    def test_for_objects_deduplicates(self) -> None:
        """同一条目只返回一次."""
        ids = [
            i.knowledge_id for i in self.service.for_objects(["插座", "插线板", "插座"])
        ]
        self.assertEqual(ids, ["electricity"])

    def test_unrelated_object_returns_empty(self) -> None:
        """无关物体不编造安全资料."""
        self.assertEqual(self.service.for_objects(["绘本"]), ())

    def test_lesson_queries_unchanged(self) -> None:
        """几何课条目与来源标注保持可查."""
        self.assertTrue(self.service.lookup("圆形"))
        self.assertTrue(all(i.source for i in self.service.lookup("热水")))

    def test_empty_query_rejected(self) -> None:
        """空白查询被拒绝."""
        with self.assertRaises(ValueError):
            _ = self.service.lookup("  ")


if __name__ == "__main__":
    _ = unittest.main()
