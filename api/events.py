# api/events.py
"""按任务编号递增的事件日志与 SSE 订阅，见 API_CONTRACT.md 第 4 节."""

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass

from api.errors import ApiError, ErrorCode
from api.schemas import JsonObject, dump_json

_CURSOR = re.compile(r"^([0-9a-f]{32}):(0|[1-9][0-9]{0,17})$")


@dataclass(frozen=True)
class TaskEvent:
    """一条已编号事件，payload 为对应资源的完整最新记录."""

    instance_id: str
    task_id: int
    sequence: int
    type: str
    payload: JsonObject
    occurred_at: float

    def envelope(self) -> JsonObject:
        """事件 data 字段使用的统一响应包装."""
        return {
            "code": 0,
            "message": "success",
            "data": {
                "instance_id": self.instance_id,
                "sequence": self.sequence,
                "task_id": self.task_id,
                "occurred_at": self.occurred_at,
                "type": self.type,
                "payload": self.payload,
            },
        }

    def to_sse(self) -> str:
        """SSE 文本；stream.reset 不占用序号，不写 id 行，客户端保留最后应用的游标."""
        head = "" if self.type == "stream.reset" else f"id: {self.cursor}\n"
        return f"{head}event: {self.type}\ndata: {dump_json(self.envelope())}\n\n"

    @property
    def cursor(self) -> str:
        """`<instance_id>:<sequence>`."""
        return f"{self.instance_id}:{self.sequence}"


class StreamResetError(Exception):
    """订阅失效，客户端须重新读取快照后再建流."""

    def __init__(self, _reason: str) -> None:
        """保存失效原因."""
        super().__init__(_reason)
        self.reason: str = _reason


def parse_cursor(value: str) -> tuple[str, int]:
    """解析事件游标，格式不合法时返回 40001.

    Raises:
        ApiError: 不是 `<instance_id>:<sequence>`.
    """
    match = _CURSOR.match(value.strip())
    if match is None:
        raise ApiError(ErrorCode.INVALID_ARGUMENT, "事件游标格式不合法")
    return match.group(1), int(match.group(2))


class EventSubscriber:
    """单个 SSE 连接的有界待发送队列；溢出即失效，不拖慢事件发布."""

    def __init__(self, _capacity: int, _replay: list[TaskEvent]) -> None:
        """以游标之后的保留事件作为初始待发送内容."""
        self._capacity: int = _capacity
        self._pending: deque[TaskEvent] = deque(_replay)
        self._wake: asyncio.Event = asyncio.Event()
        self._reset_reason: str | None = None
        if _replay:
            self._wake.set()

    @property
    def closed(self) -> bool:
        """已失效或已关闭."""
        return self._reset_reason is not None

    def push(self, event: TaskEvent) -> None:
        """非阻塞投递实时事件."""
        if self.closed:
            return
        if len(self._pending) >= self._capacity:
            self.close("subscriber_slow")
            return
        self._pending.append(event)
        self._wake.set()

    def close(self, reason: str) -> None:
        """丢弃未发送事件并唤醒等待者，重复关闭保留首个原因."""
        if self.closed:
            return
        self._reset_reason = reason
        self._pending.clear()
        self._wake.set()

    async def next(self, timeout_s: float) -> TaskEvent | None:
        """取下一条事件；超时返回 None 供发送心跳.

        Raises:
            StreamResetError: 订阅已失效.
        """
        while True:
            if self._pending:
                return self._pending.popleft()
            if self._reset_reason is not None:
                raise StreamResetError(self._reset_reason)
            self._wake.clear()
            try:
                async with asyncio.timeout(timeout_s):
                    _ = await self._wake.wait()
            except TimeoutError:
                return None


class TaskEventLog:
    """单个任务的事件序号、最近事件保留与订阅者广播；只在事件循环线程使用."""

    def __init__(
        self,
        _instance_id: str,
        _task_id: int,
        _retention: int = 1000,
        _subscriber_capacity: int = 256,
    ) -> None:
        """配置保留条数与每个订阅者的实时积压上限."""
        self._instance_id: str = _instance_id
        self._task_id: int = _task_id
        self._events: deque[TaskEvent] = deque(maxlen=_retention)
        # 重放内容可能占满保留条数，积压上限在其之上另加实时余量。
        self._subscriber_capacity: int = _retention + _subscriber_capacity
        self._subscribers: set[EventSubscriber] = set()
        self.sequence: int = 0

    @property
    def cursor(self) -> str:
        """当前游标，快照与内容在同一同步段内生成."""
        return f"{self._instance_id}:{self.sequence}"

    def publish(self, event_type: str, payload: JsonObject) -> None:
        """分配下一个序号、保留并广播."""
        self.sequence += 1
        event = TaskEvent(
            self._instance_id,
            self._task_id,
            self.sequence,
            event_type,
            payload,
            time.time(),
        )
        self._events.append(event)
        for subscriber in tuple(self._subscribers):
            subscriber.push(event)
            if subscriber.closed:
                self._subscribers.discard(subscriber)

    def subscribe(self, instance_id: str, after: int) -> EventSubscriber:
        """从游标之后开始订阅；游标失效时返回只含重置原因的订阅."""
        reason: str | None = None
        oldest = self._events[0].sequence if self._events else self.sequence + 1
        if instance_id != self._instance_id:
            reason = "instance_changed"
        elif after > self.sequence:
            reason = "cursor_invalid"
        elif after < oldest - 1:
            reason = "cursor_expired"
        if reason is not None:
            subscriber = EventSubscriber(self._subscriber_capacity, [])
            subscriber.close(reason)
            return subscriber
        # 重放与登记之间没有 await，不会漏掉或重复实时事件。
        replay = [event for event in self._events if event.sequence > after]
        subscriber = EventSubscriber(self._subscriber_capacity, replay)
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        """连接结束时释放订阅."""
        self._subscribers.discard(subscriber)

    def reset_event(self, reason: str) -> TaskEvent:
        """构造不占用序号的 stream.reset 事件."""
        return TaskEvent(
            self._instance_id,
            self._task_id,
            self.sequence,
            "stream.reset",
            {"reason": reason},
            time.time(),
        )

    def close(self, reason: str) -> None:
        """服务关闭或任务记录被清理时让所有订阅者重置."""
        for subscriber in tuple(self._subscribers):
            subscriber.close(reason)
        self._subscribers.clear()
