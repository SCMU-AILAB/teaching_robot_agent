# runtime/events.py
"""单消费者 Runtime 事件到多个本地订阅者的广播桥."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from domain.models import ActionEvent
from domain.validation import positive_int
from runtime.action_manager import ActionManager


class EventSubscription:
    """有界订阅，消费者过慢时明确要求重新获取状态."""

    def __init__(self, _capacity: int) -> None:
        """初始化有界队列，不启动后台任务."""
        self._queue: asyncio.Queue[ActionEvent | None] = asyncio.Queue(_capacity)
        self._closed: bool = False
        self._overflow: bool = False

    def publish(self, event: ActionEvent) -> None:
        """非阻塞投递，溢出不拖慢执行器或其他订阅者."""
        if self._closed:
            return
        if self._queue.full():
            self._overflow = True
            self.close()
            return
        self._queue.put_nowait(event)

    def close(self) -> None:
        """唤醒等待者并释放排队事件，重复关闭安全."""
        if self._closed:
            return
        self._closed = True
        while not self._queue.empty():
            _ = self._queue.get_nowait()
        self._queue.put_nowait(None)

    async def next_event(self) -> ActionEvent:
        """获取下一事件，关闭或溢出后抛出明确异常."""
        if self._overflow:
            raise RuntimeError("Event subscriber overflow; refresh snapshot")
        if self._closed:
            raise EOFError("Subscription closed")
        event = await self._queue.get()
        if self._overflow:
            raise RuntimeError("Event subscriber overflow; refresh snapshot")
        if event is None:
            raise EOFError("Subscription closed")
        return event


class ActionEventHub:
    """独占 next_event 并广播，生命周期由应用显式管理."""

    def __init__(self, _runtime: ActionManager, _capacity: int = 128) -> None:
        """注入运行时并配置各订阅队列容量."""
        self._runtime: ActionManager = _runtime
        self._capacity: int = positive_int(_capacity, "capacity")
        self._subscribers: set[EventSubscription] = set()
        self._worker: asyncio.Task[None] | None = None
        self._closed: bool = False

    async def start(self) -> None:
        """启动唯一读取协程；同一 Runtime 只能装配一个广播器."""
        if self._closed:
            raise RuntimeError("Event hub is closed")
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="action-event-hub")

    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[EventSubscription]:
        """订阅后开始接收事件，退出上下文自动释放队列."""
        if self._closed:
            raise RuntimeError("Event hub is closed")
        subscription = EventSubscription(self._capacity)
        self._subscribers.add(subscription)
        try:
            yield subscription
        finally:
            self._subscribers.discard(subscription)
            subscription.close()

    async def _run(self) -> None:
        """顺序转发原始状态事件，不把延迟读取到的最新记录当作历史状态."""
        while True:
            event = await self._runtime.next_event()
            for subscription in tuple(self._subscribers):
                subscription.publish(event)

    async def close(self) -> None:
        """结束读取并唤醒所有订阅者，不取消机器人动作."""
        self._closed = True
        if self._worker is not None:
            _ = self._worker.cancel()
            _ = await asyncio.gather(self._worker, return_exceptions=True)
        for subscription in self._subscribers:
            subscription.close()
        self._subscribers.clear()
