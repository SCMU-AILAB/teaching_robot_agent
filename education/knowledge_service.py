# education/knowledge_service.py
"""不依赖课程会话的家庭学习与安全知识查询."""

from collections.abc import Iterable

from domain.education import KnowledgePoint
from domain.validation import nonempty_string
from education.knowledge import build_shapes_lesson


def _score_entry(entry: KnowledgePoint, query: str) -> int:
    """按标题、关键词、关联物体与子串命中为条目计分."""
    score = 0
    title = entry.title.casefold()
    if title in query:
        score += 3
    score += 2 * sum(1 for keyword in entry.keywords if keyword.casefold() in query)
    score += 2 * sum(1 for item in entry.related_objects if item.casefold() in query)
    # 兼容原有子串匹配，使课程查询（如“圆形”）继续有效
    if query in title or query in entry.explanation.casefold():
        score += 1
    return score


class KnowledgeService:
    """按主题检索带来源的资料，不创建问题或学习进度."""

    def __init__(self, _entries: tuple[KnowledgePoint, ...]) -> None:
        """注入知识条目，资料审核与维护独立于任务运行."""
        self._entries: tuple[KnowledgePoint, ...] = _entries

    def lookup(self, query: str) -> tuple[KnowledgePoint, ...]:
        """按得分排序匹配知识条目，未找到时返回空结果而非编造来源."""
        query = nonempty_string(query, "query").strip().casefold()
        scored = [(_score_entry(entry, query), entry) for entry in self._entries]
        # 得分相同时按编号排序，保证结果顺序稳定
        ranked = sorted(
            (item for item in scored if item[0] > 0),
            key=lambda item: (-item[0], item[1].knowledge_id),
        )
        return tuple(entry for _, entry in ranked)

    def for_objects(self, labels: Iterable[str]) -> tuple[KnowledgePoint, ...]:
        """把物体标签关联到安全条目，按标签顺序首次出现去重."""
        linked: dict[str, KnowledgePoint] = {}
        for label in labels:
            text = label.strip().casefold()
            if not text:
                continue
            for entry in self._entries:
                if entry.knowledge_id in linked:
                    continue
                # 双向包含：容忍“白色电热水壶”这类带修饰词的标签
                if any(
                    item.casefold() in text or text in item.casefold()
                    for item in entry.related_objects
                ):
                    linked[entry.knowledge_id] = entry
        return tuple(linked.values())


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
                ("烫", "热水", "开水", "烫伤"),
                ("热水壶", "水壶", "电热水壶", "保温杯", "热水杯", "茶壶"),
            ),
            KnowledgePoint(
                "electricity",
                "插座与用电安全",
                "不要将手指或物品插入插座，不用湿手触碰电器；发现破损电线应远离并告知成年人。"
                + "图片不能确认设备是否带电。",
                source,
                ("插座", "电", "触电", "电线"),
                ("插座", "插线板", "插头", "电线", "充电器"),
            ),
            KnowledgePoint(
                "knife",
                "刀具与锋利物品",
                "刀和剪刀很锋利，不要拿来玩；需要使用时请成年人帮忙，用完放回固定位置。",
                source,
                ("刀", "锋利", "割伤"),
                ("刀", "剪刀", "水果刀", "菜刀"),
            ),
            KnowledgePoint(
                "medicine",
                "药品安全",
                "药品不是糖果，不要自己打开或吃药；需要用药时由成年人按说明给药。",
                source,
                ("药", "药片", "吃药"),
                ("药", "药瓶", "药盒", "胶囊"),
            ),
            KnowledgePoint(
                "gas_stove",
                "燃气灶与厨房用火",
                "不要自己开燃气灶或玩打火机；闻到异味应离开厨房并告诉成年人。"
                + "图片不能判断是否漏气。",
                source,
                ("火", "燃气", "煤气", "灶"),
                ("燃气灶", "灶台", "炉灶", "打火机"),
            ),
            KnowledgePoint(
                "cleaner",
                "清洁剂与化学品",
                "清洁剂和消毒液不能喝也不能闻，沾到皮肤或眼睛要马上告诉成年人。",
                source,
                ("清洁剂", "消毒液", "洗洁精"),
                ("清洁剂", "消毒液", "洗洁精", "洗衣液"),
            ),
            KnowledgePoint(
                "window",
                "窗户与阳台安全",
                "不要爬窗台或阳台护栏，不在窗边追逐打闹；开窗请成年人帮忙。",
                source,
                ("窗户", "阳台", "爬", "掉下"),
                ("窗户", "阳台", "窗台", "护栏"),
            ),
        )
    )
