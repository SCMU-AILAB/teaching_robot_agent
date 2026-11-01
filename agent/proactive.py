# agent/proactive.py
"""主动观察：机器人空闲时定期看一眼，发现未提过且资料库有安全条目的物体就主动提醒."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from agent.exploration import Narrator, same_object
from app.team_gateway import TeamGateway
from domain.models import ActionStatus
from domain.services import ObservationRequest
from education.knowledge_service import KnowledgeService
from skills.speak import speech_budget_s
from speech.text import speakable_text

logger = logging.getLogger(__name__)

_LOOK_QUESTION = "看看周围有什么物体？"


@dataclass(frozen=True)
class ProactiveConfig:
    """观察间隔与提醒间隔，秒."""

    interval_s: float = 10.0
    cooldown_s: float = 30.0

    def __post_init__(self) -> None:
        """间隔须为正."""
        if self.interval_s <= 0 or self.cooldown_s <= 0:
            raise ValueError("Proactive intervals must be positive")


@dataclass
class _TaskMemory:
    """单个任务已经提过的物体与最近一次提醒时间."""

    mentioned: list[str] = field(default_factory=list)
    last_spoken: float = float("-inf")


class ProactiveWatcher:
    """后台循环；开关、任务与空闲状态都在每轮读取，构造时不启动."""

    def __init__(
        self,
        _gateway: TeamGateway,
        _knowledge: KnowledgeService,
        _narrator: Narrator,
        *,
        _enabled: Callable[[], bool],
        _active_task: Callable[[], int | None],
        _idle: Callable[[int], bool],
        _config: ProactiveConfig | None = None,
        _clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """注入网关、资料库、讲解生成，以及读取开关、当前任务与宿主空闲状态的回调."""
        self._gateway: TeamGateway = _gateway
        self._knowledge: KnowledgeService = _knowledge
        self._narrator: Narrator = _narrator
        self._enabled: Callable[[], bool] = _enabled
        self._active_task: Callable[[], int | None] = _active_task
        self._idle: Callable[[int], bool] = _idle
        self._config: ProactiveConfig = _config or ProactiveConfig()
        self._clock: Callable[[], float] = _clock
        self._memory: dict[int, _TaskMemory] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """启动后台循环，重复调用安全."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="proactive-watcher")

    async def close(self) -> None:
        """停止后台循环并等待进行中的提醒结束或取消."""
        task, self._task = self._task, None
        if task is not None:
            _ = task.cancel()
            _ = await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        """每个间隔检查一次；单轮异常只记日志."""
        while True:
            await asyncio.sleep(self._config.interval_s)
            try:
                _ = await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[ProactiveWatcher._run] 主动观察失败")

    async def check_once(self) -> str | None:
        """满足条件时观察一次并可能提醒；返回说出的话，没说时为 None."""
        task_id = self._active_task()
        if not self._enabled() or task_id is None or not self._idle(task_id):
            return None
        memory = self._memory.setdefault(task_id, _TaskMemory())
        if self._clock() - memory.last_spoken < self._config.cooldown_s:
            return None
        # ========== Step1: 观察，挑出未提过且资料库有条目的物体 ==========
        observation = await self._gateway.observe(
            ObservationRequest(uuid4().hex, task_id, _LOOK_QUESTION, 0)
        )
        fresh: list[tuple[str, str]] = []
        for item in observation.analysis.objects:
            if any(same_object(item.label, known) for known in memory.mentioned):
                continue
            if any(same_object(item.label, label) for label, _ in fresh):
                continue
            if not self._knowledge.for_objects((item.label,)):
                continue
            fresh.append((item.label, dict(item.attributes).get("position", "不确定")))
        if not fresh:
            return None
        chosen = fresh[:2]
        entries = self._knowledge.for_objects(label for label, _ in chosen)
        text = await self._narrator(chosen, entries)
        # ========== Step2: 生成期间孩子开口或任务变化就放弃，物体留待下次 ==========
        if (
            not text
            or not self._enabled()
            or self._active_task() != task_id
            or not self._idle(task_id)
        ):
            return None
        memory.mentioned.extend(label for label, _ in chosen)
        memory.last_spoken = self._clock()
        spoken = speakable_text(text)
        if spoken:
            record = await self._gateway.submit_action(
                task_id,
                "speak",
                {"text": text},
                timeout_s=speech_budget_s(spoken) + 5.0,
            )
            result = await self._gateway.wait_for_action(task_id, record.action_id)
            if result.status != ActionStatus.SUCCEEDED:
                self._gateway.acknowledge_failures(task_id, (record.action_id,))
        logger.info(
            "[ProactiveWatcher.check_once] 主动提醒 task_id=%s objects=%s text=%s",
            task_id,
            [label for label, _ in chosen],
            text,
        )
        return text

    def forget(self, task_id: int) -> None:
        """任务结束后清理记录."""
        _ = self._memory.pop(task_id, None)
