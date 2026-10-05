# education/knowledge_service.py
"""不依赖课程会话的家庭学习与安全知识查询."""

from domain.education import KnowledgePoint
from domain.validation import nonempty_string
from education.knowledge import build_shapes_lesson


class KnowledgeService:
    """按主题检索带来源的资料，不创建问题或学习进度."""

    def __init__(self, _entries: tuple[KnowledgePoint, ...]) -> None:
        """注入知识条目，资料审核与维护独立于任务运行."""
        self._entries: tuple[KnowledgePoint, ...] = _entries

    def lookup(self, query: str) -> tuple[KnowledgePoint, ...]:
        """匹配知识标题与内容，未找到时返回空结果而非编造来源."""
        query = nonempty_string(query, "query").strip().casefold()
        return tuple(
            entry
            for entry in self._entries
            if query in entry.title.casefold() or query in entry.explanation.casefold()
        )


def build_home_knowledge() -> KnowledgeService:
    """提供开发用家庭教育条目，正式用于儿童前仍需内容审核."""
    source = "项目人工编写的安全教育示例；待负责人审核，非现场风险检测结论"
    return KnowledgeService(
        (
            *(step.knowledge for step in build_shapes_lesson().steps),
            KnowledgePoint(
                "hot_water",
                "热水与烫伤预防",
                "不要触碰热水壶和热水杯，不拉扯电线；需要取热水时请成年人帮助。"
                + "仅凭外观不能判断杯中液体温度。",
                source,
            ),
            KnowledgePoint(
                "electricity",
                "插座与用电安全",
                "不要将手指或物品插入插座，不用湿手触碰电器；发现破损电线应远离并告知成年人。"
                + "图片不能确认设备是否带电。",
                source,
            ),
        )
    )
