# agent/robot_host.py
"""家庭机器人通用任务宿主，不依赖课堂或学生成绩."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from agent.interfaces import DecisionAgent
from app.team_gateway import TeamGateway, UserInput
from domain.models import ActionStatus, TaskState, TaskStatus
from domain.services import ObservationRequest
from domain.validation import positive_int


@dataclass(frozen=True)
class RobotReply:
    """任务输出与提交时的状态，动作编号仅记录已等待的实际动作."""

    task_id: int
    text: str
    task_status: TaskStatus
    action_ids: tuple[int, ...] = ()
    rejected: bool = False


class RobotHost:
    """所有输入由任务策略处理，停止绕过消费锁."""

    def __init__(
        self,
        _gateway: TeamGateway,
        _agent_factory: Callable[[int], DecisionAgent],
        _max_actions: int = 3,
    ) -> None:
        """注入共享服务，采用串行输入与有限动作续接策略."""
        self._gateway: TeamGateway = _gateway
        self._factory: Callable[[int], DecisionAgent] = _agent_factory
        self._max_actions: int = positive_int(_max_actions, "max_actions")
        self._lock: asyncio.Lock = asyncio.Lock()
        self._agents: dict[int, DecisionAgent] = {}
        self._seen: dict[str, UserInput] = {}
        self._replies: dict[str, RobotReply] = {}
        self._workers: dict[int, asyncio.Task[RobotReply]] = {}
        self._stopped: set[int] = set()
        self._closed: bool = False

    async def start(self, task_id: int) -> RobotReply:
        """登记机器人任务，不创建课程或后台输入消费者."""
        async with self._lock:
            await self._require_active(task_id)
            if task_id not in self._agents:
                self._agents[task_id] = self._factory(task_id)
            return await self._reply(task_id, "机器人任务已就绪。")

    async def process_next(self) -> RobotReply:
        """由应用唯一消费者循环调用，等待动作期间后续输入保留在队列."""
        return await self.handle(await self._gateway.next_input())

    async def handle(self, item: UserInput) -> RobotReply:
        """处理一条输入并等待其动作链结束，成功重试返回原结果.

        Args:
            item: 入口生成的文字或最终语音输入，课程答案须交给课程模式。

        Returns:
            交互状态及最终输出；不输出动作接受时的模型完成声明。

        Raises:
            ValueError: 未启动、输入冲突、任务停止或失败输入被重放。
            RuntimeError: 模型重复动作或超出续接上限。
            Exception: 模型或工具失败；宿主先取消任务动作再向外传播。
        """
        async with self._lock:
            # ========== Step1: 去重在副作用之前完成，失败输入不自动重放 ==========
            previous = self._seen.get(item.input_id)
            if previous is not None:
                if previous != item:
                    raise ValueError("Input ID reused with different content")
                if item.input_id in self._replies:
                    return self._replies[item.input_id]
                raise ValueError("Previous processing failed; do not replay input")
            await self._require_active(item.task_id)
            if item.task_id not in self._agents:
                raise ValueError("Start the robot before handling input")
            if item.question_id is not None:
                raise ValueError("Use explicit teaching mode for question answers")
            self._seen[item.input_id] = item
            worker = asyncio.create_task(self._handle(item), name="robot-input")
            self._workers[item.task_id] = worker
            try:
                reply = await worker
                self._replies[item.input_id] = reply
                return reply
            except BaseException:
                # 输入处理被取消也不能遗留已接受的机器人动作。
                self._stopped.add(item.task_id)
                _ = await self._gateway.cancel_task(item.task_id)
                raise
            finally:
                _ = self._workers.pop(item.task_id, None)

    async def cancel(self, task_id: int) -> TaskState:
        """停止不等待消费锁或模型返回，执行层负责确认设备停止."""
        self._stopped.add(task_id)
        worker = self._workers.get(task_id)
        if worker is not None:
            _ = worker.cancel()
        return await self._gateway.cancel_task(task_id)

    async def close(self) -> None:
        """结束所有已启动任务并等待本宿主的工作协程释放."""
        self._closed = True
        workers = tuple(self._workers.values())
        for task_id in tuple(self._agents):
            _ = await self.cancel(task_id)
        if workers:
            _ = await asyncio.gather(*workers, return_exceptions=True)

    async def _require_active(self, task_id: int) -> None:
        """阻止停止之后的新交互及迟到模型输出."""
        snapshot = await self._gateway.get_snapshot(task_id)
        if (
            self._closed
            or task_id in self._stopped
            or snapshot.task.status
            not in {
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
            }
        ):
            raise ValueError("Robot is no longer active")

    async def _reply(
        self,
        task_id: int,
        text: str,
        action_ids: tuple[int, ...] = (),
        rejected: bool = False,
    ) -> RobotReply:
        """汇总本轮处理之后的交互与执行状态."""
        snapshot = await self._gateway.get_snapshot(task_id)
        return RobotReply(
            task_id,
            text,
            snapshot.task.status,
            action_ids,
            rejected,
        )

    async def _handle(self, item: UserInput) -> RobotReply:
        """处理通用请求，动作完成后按最新快照续接模型."""
        # ========== Step2: 执行一次输入对应的有限动作链 ==========
        agent = self._agents[item.task_id]
        turn = await agent.decide(item)
        actions: list[int] = []
        while True:
            await self._require_active(item.task_id)
            action_id = turn.pending_action_id
            if action_id is None:
                _ = self._gateway.finish_interaction(item.task_id)
                return await self._reply(item.task_id, turn.text, tuple(actions))
            if action_id in actions or len(actions) >= self._max_actions:
                raise RuntimeError("Repeated action or robot action limit exceeded")
            actions.append(action_id)
            result = await self._gateway.wait_for_action(item.task_id, action_id)
            await self._require_active(item.task_id)
            if result.status != ActionStatus.SUCCEEDED:
                self._stopped.add(item.task_id)
                _ = await self._gateway.cancel_task(item.task_id)
                return await self._reply(
                    item.task_id,
                    f"动作未成功，状态为 {result.status.value}，已结束本次任务。",
                    tuple(actions),
                )
            if len(actions) == self._max_actions:
                self._stopped.add(item.task_id)
                _ = await self._gateway.cancel_task(item.task_id)
                return await self._reply(
                    item.task_id,
                    "已达到连续动作上限，本次任务已停止；不代表整体目标已完成。",
                    tuple(actions),
                )
            # 不重发原输入，避免模型把相同移动指令再次当作新命令。
            previous_observation = self._gateway.latest_observation(item.task_id)
            observation = None
            if previous_observation is not None:
                observation = await self._gateway.observe(
                    ObservationRequest(
                        uuid4().hex,
                        item.task_id,
                        previous_observation.question,
                        max_age_s=2,
                    )
                )
            turn = await agent.decide(observation=observation)
