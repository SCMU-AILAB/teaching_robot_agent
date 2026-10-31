# agent/robot_host.py
"""家庭机器人通用任务宿主，不依赖课堂或学生成绩."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from agent.embodied_agent import AgentTurn
from agent.exploration import EXPLORE_REPLY, ExplorationController, wants_exploration
from agent.interfaces import DecisionAgent
from app.team_gateway import TeamGateway, UserInput
from domain.models import ActionRecord, ActionStatus, TaskState, TaskStatus
from domain.services import ObservationRequest
from domain.validation import positive_int
from skills.speak import speech_budget_s
from speech.text import speakable_text

logger = logging.getLogger(__name__)

_INPUT_RETENTION = 4096


@dataclass(frozen=True)
class RobotReply:
    """任务输出与提交时的状态，动作编号仅记录已等待的实际动作."""

    task_id: int
    text: str
    task_status: TaskStatus
    action_ids: tuple[int, ...] = ()
    rejected: bool = False


def failure_reply(record: ActionRecord) -> str:
    """动作失败时给孩子的说明；遇障停车说出原因."""
    failure = record.failure or ""
    marker = "ObstacleError:"
    if marker in failure:
        reason = failure.split(marker, 1)[1].split(";", 1)[0].strip()
        return f"{reason}，换个方向试试吧。"
    return f"动作未成功（{record.status.value}），已停下，可以换个说法再试。"


class RobotHost:
    """所有输入由任务策略处理，停止绕过消费锁."""

    def __init__(
        self,
        _gateway: TeamGateway,
        _agent_factory: Callable[[int], DecisionAgent],
        _max_actions: int = 3,
        *,
        _speak_replies: bool = False,
        _exploration_factory: Callable[[int], ExplorationController] | None = None,
    ) -> None:
        """注入共享服务，采用串行输入与有限动作续接策略.

        Args:
            _gateway: 核心网关.
            _agent_factory: 按任务创建决策策略.
            _max_actions: 一条输入最多续接的动作数.
            _speak_replies: 是否播报最终回答.
            _exploration_factory: 按任务创建探索控制器；None 时不支持探索.
        """
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
        self._speak_replies: bool = _speak_replies
        self._exploration_factory: Callable[[int], ExplorationController] | None = (
            _exploration_factory
        )
        self._explore_requests: set[int] = set()
        self._explorations: dict[int, asyncio.Task[None]] = {}

    async def start(self, task_id: int) -> RobotReply:
        """登记机器人任务，不创建课程或后台输入消费者."""
        async with self._lock:
            await self._require_active(task_id)
            if task_id not in self._agents:
                self._agents[task_id] = self._factory(task_id)
            return await self._reply(task_id, "机器人任务已就绪。")

    def request_exploration(self, task_id: int) -> None:
        """模型调用探索工具时登记；本条回答结束后在后台开始.

        Raises:
            ValueError: 未装配探索。
        """
        if self._exploration_factory is None:
            raise ValueError("Exploration is not available")
        self._explore_requests.add(task_id)

    async def process_next(self) -> RobotReply:
        """由应用唯一消费者循环调用，等待动作期间后续输入保留在队列."""
        item = await self._gateway.next_input(include_terminal=True)
        snapshot = await self._gateway.get_snapshot(item.task_id)
        if snapshot.task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            return await self._reply(
                item.task_id, "任务已结束，本条输入未执行。", rejected=True
            )
        return await self.handle(item)

    async def handle(self, item: UserInput) -> RobotReply:
        """处理一条输入并等待其动作链结束，成功重试返回原结果.

        Args:
            item: 入口生成的文字或最终语音输入，课程答案须交给课程模式。

        Returns:
            交互状态及最终输出；不输出动作接受时的模型完成声明。

        Raises:
            ValueError: 未启动、输入冲突、任务停止或失败输入被重放。
            RuntimeError: 模型重复动作。
            Exception: 模型或工具失败；宿主先停止本条输入提交的动作再向外传播，
                任务保留，设备停止无法确认时才结束任务。
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
            # 孩子说话先打断探索，等它停下当前动作再处理
            await self._stop_exploration(item.task_id)
            self._explore_requests.discard(item.task_id)
            if item.question_id is not None:
                raise ValueError("Use explicit teaching mode for question answers")
            while len(self._seen) >= _INPUT_RETENTION:
                # 只保留最近的去重记录；长期运行不能因累计输入数而拒绝服务。
                oldest = next(iter(self._seen))
                del self._seen[oldest]
                _ = self._replies.pop(oldest, None)
            self._seen[item.input_id] = item
            before = {
                record.action_id
                for record in (await self._gateway.get_snapshot(item.task_id)).actions
            }
            worker = asyncio.create_task(self._handle(item), name="robot-input")
            self._workers[item.task_id] = worker
            try:
                reply = await worker
                self._replies[item.input_id] = reply
                if item.task_id in self._explore_requests:
                    self._explore_requests.discard(item.task_id)
                    self._start_exploration(item.task_id)
                return reply
            except Exception:
                self._explore_requests.discard(item.task_id)
                # 模型或工具失败只作废本条输入：停止它提交的动作，任务继续接受下一句。
                if item.task_id in self._stopped or not await self._abandon_turn(
                    item.task_id, before
                ):
                    await self._stop_task(item.task_id)
                raise
            except BaseException:
                # 停止任务或宿主关闭取消了处理，也不能遗留已接受的机器人动作。
                await self._stop_task(item.task_id)
                raise
            finally:
                _ = self._workers.pop(item.task_id, None)
                status = (await self._gateway.get_snapshot(item.task_id)).task.status
                if status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                    _ = self._agents.pop(item.task_id, None)

    async def cancel(self, task_id: int) -> TaskState:
        """停止不等待消费锁或模型返回，执行层负责确认设备停止."""
        self._stopped.add(task_id)
        worker = self._workers.get(task_id)
        if worker is not None:
            _ = worker.cancel()
        exploration = self._explorations.pop(task_id, None)
        if exploration is not None:
            _ = exploration.cancel()
        result = await self._gateway.cancel_task(task_id)
        _ = self._agents.pop(task_id, None)
        if exploration is not None:
            _ = await asyncio.gather(exploration, return_exceptions=True)
        return result

    async def finish(self, task_id: int) -> TaskState:
        """显式验收任务，拒绝处理中的任务，释放已结束任务的策略对象."""
        if self._lock.locked():
            raise RuntimeError("Wait for request processing before finishing")
        async with self._lock:
            await self._require_active(task_id)
            result = self._gateway.finish_interaction(task_id)
            _ = self._agents.pop(task_id, None)
            return result

    async def close(self) -> None:
        """结束所有已启动任务并等待本宿主的工作协程释放."""
        self._closed = True
        workers = tuple(self._workers.values())
        for task_id in tuple(self._agents):
            _ = await self.cancel(task_id)
        for task_id in tuple(self._explorations):
            await self._stop_exploration(task_id)
        if workers:
            _ = await asyncio.gather(*workers, return_exceptions=True)

    def _start_exploration(self, task_id: int) -> None:
        """在后台运行探索；同一任务只保留一个."""
        assert self._exploration_factory is not None
        controller = self._exploration_factory(task_id)
        self._explorations[task_id] = asyncio.create_task(
            self._explore(task_id, controller), name="robot-exploration"
        )

    async def _explore(self, task_id: int, controller: ExplorationController) -> None:
        """探索异常只结束探索；执行层锁定时结束任务."""
        try:
            _ = await controller.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[RobotHost._explore] 探索中断 task_id=%s", task_id)
            snapshot = await self._gateway.get_snapshot(task_id)
            if snapshot.blocked_reason is not None:
                await self._stop_task(task_id)
        finally:
            if self._explorations.get(task_id) is asyncio.current_task():
                del self._explorations[task_id]

    async def _stop_exploration(self, task_id: int) -> None:
        """取消探索并等待它停下进行中的动作."""
        exploration = self._explorations.pop(task_id, None)
        if exploration is not None:
            _ = exploration.cancel()
            _ = await asyncio.gather(exploration, return_exceptions=True)

    def exploring(self, task_id: int) -> bool:
        """报告任务是否正在探索."""
        return task_id in self._explorations

    async def _stop_task(self, task_id: int) -> None:
        """结束整个任务：用户停止、宿主关闭或设备停止无法确认时使用."""
        self._stopped.add(task_id)
        _ = await self._gateway.cancel_task(task_id)

    async def _abandon_turn(self, task_id: int, before: set[int]) -> bool:
        """作废本条输入：取消并等待它提交的动作，清空策略的本轮状态.

        Args:
            task_id: 任务编号.
            before: 本条输入开始前任务已有的动作编号.

        Returns:
            任务仍可继续交互时为 True；执行层因停止确认失败而锁定时为 False.
        """
        snapshot = await self._gateway.get_snapshot(task_id)
        started = [
            record.action_id
            for record in snapshot.actions
            if record.action_id not in before
        ]
        for action_id in started:
            _ = await self._gateway.cancel_action(task_id, action_id)
        for action_id in started:
            _ = await self._gateway.wait_for_action(task_id, action_id)
        agent = self._agents.get(task_id)
        if agent is not None:
            agent.abandon_turn()
        snapshot = await self._gateway.get_snapshot(task_id)
        recovered = snapshot.blocked_reason is None and snapshot.task.status in {
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
        }
        if recovered:
            self._gateway.acknowledge_failures(task_id, started)
        return recovered

    async def _end_turn(self, task_id: int) -> None:
        """动作链未正常结束：作废本轮；设备停止无法确认时结束任务."""
        if not await self._abandon_turn(task_id, set()):
            await self._stop_task(task_id)

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

    async def _say(self, task_id: int, text: str, actions: list[int]) -> RobotReply:
        """装配语音时播报回答再返回；播报失败保留文字，扬声器停止无法确认时结束任务."""
        if self._speak_replies and text.strip():
            # 期限按朗读长度给出，另留清理余量；固定值会让长回答必然超时。
            record = await self._gateway.submit_action(
                task_id,
                "speak",
                {"text": text},
                timeout_s=speech_budget_s(speakable_text(text)) + 5.0,
            )
            actions.append(record.action_id)
            spoken = await self._gateway.wait_for_action(task_id, record.action_id)
            await self._require_active(task_id)
            if spoken.status != ActionStatus.SUCCEEDED:
                # 播报失败不影响文字回答；只有扬声器停止无法确认才结束任务。
                if (
                    await self._gateway.get_snapshot(task_id)
                ).blocked_reason is not None:
                    await self._stop_task(task_id)
                else:
                    self._gateway.acknowledge_failures(task_id, (record.action_id,))
                return await self._reply(
                    task_id,
                    text + "（语音播报未完成，文字回答已保留。）",
                    tuple(actions),
                )
        return await self._reply(task_id, text, tuple(actions))

    async def _handle(self, item: UserInput) -> RobotReply:
        """处理通用请求，动作完成后按最新快照续接模型."""
        # ========== Step2: 执行一次输入对应的有限动作链 ==========
        agent = self._agents[item.task_id]
        if self._exploration_factory is not None and wants_exploration(item.text):
            # 明确的探索请求不经模型：9B 模型常只口头答应而不调用探索工具
            logger.info("[RobotHost._handle] 识别为探索请求 task_id=%s", item.task_id)
            self._explore_requests.add(item.task_id)
            agent.record_exchange(item.text, EXPLORE_REPLY, moved=True)
            turn = AgentTurn(EXPLORE_REPLY)
        else:
            turn = await agent.decide(item)
        actions: list[int] = []
        while True:
            await self._require_active(item.task_id)
            action_id = turn.pending_action_id
            if action_id is None:
                # 模型文字不是目标达成证据；任务保留，允许澄清和宿主显式验收。
                return await self._say(item.task_id, turn.text, actions)
            if action_id in actions or len(actions) >= self._max_actions:
                raise RuntimeError("Repeated action or robot action limit exceeded")
            actions.append(action_id)
            result = await self._gateway.wait_for_action(item.task_id, action_id)
            await self._require_active(item.task_id)
            if result.status != ActionStatus.SUCCEEDED:
                await self._end_turn(item.task_id)
                text = failure_reply(result)
                if item.task_id in self._stopped:
                    return await self._reply(item.task_id, text, tuple(actions))
                # 停车原因要说出来，孩子才知道车为什么不动了
                return await self._say(item.task_id, text, actions)
            if len(actions) == self._max_actions:
                await self._end_turn(item.task_id)
                return await self._reply(
                    item.task_id,
                    "已连续执行多个动作，先停在这里；不代表整体目标已完成。",
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
