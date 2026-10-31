# app/robot_application.py
"""赛题主应用装配：通用任务、感知、设备、知识与单个决策 Agent."""

from collections.abc import Callable

from langchain_core.language_models import BaseChatModel

from agent.context import RobotContextBuilder
from agent.embodied_agent import EmbodiedAgent
from agent.exploration import (
    ExplorationConfig,
    ExplorationController,
    model_narrator,
)
from agent.robot_host import RobotHost, RobotReply
from agent.tools import ToolAdapter
from app.audio import AudioAdapters
from app.team_gateway import TeamGateway, UserInput
from domain.models import TaskState
from domain.robot import RobotCapability
from education.knowledge_service import KnowledgeService, build_home_knowledge
from perception.interfaces import PerceptionService
from robot.base import RobotAdapter
from robot.obstacle_guard import GuardedRobotAdapter
from runtime.action_manager import ActionManager
from runtime.events import ActionEventHub
from skills.robot_registry import build_robot_skills
from skills.speak import SpeakSkill
from speech.input import SpeechInputService
from speech.output import SpeechOutputService
from storage.evidence import EvidenceStore


class RobotApplication:
    """持有服务生命周期；课程不是启动机器人的前置条件."""

    def __init__(
        self,
        _robot: RobotAdapter,
        _perception: PerceptionService,
        _model: BaseChatModel,
        _knowledge: KnowledgeService | None = None,
        *,
        _audio: AudioAdapters | None = None,
        _evidence: EvidenceStore | None = None,
        _answer_max_chars: int | None = None,
        _exploration: ExplorationConfig | None = None,
    ) -> None:
        """显式注入设备、感知与模型，构造期间不连接外部服务.

        Args:
            _robot: 机器人适配器.
            _perception: 感知服务.
            _model: 决策模型.
            _knowledge: 知识服务，缺省使用家庭知识库.
            _audio: 可选语音适配器.
            _evidence: 感知与语音共用的证据存储；提供时按任务和语音作业释放证据.
            _answer_max_chars: 最终回答的大致字数上限，None 不限制.
            _exploration: 探索设置；底盘带深度避障且能前进、转向、定位时才生效.
        """
        self.runtime: ActionManager = ActionManager(_robot, build_robot_skills(_robot))
        self.gateway: TeamGateway = TeamGateway(
            self.runtime, _perception, _evidence=_evidence
        )
        self._perception: PerceptionService = _perception
        self._model: BaseChatModel = _model
        self._knowledge: KnowledgeService = _knowledge or build_home_knowledge()
        self._answer_max_chars: int | None = _answer_max_chars
        self.speech_input: SpeechInputService | None = None
        self._speech_output: SpeechOutputService | None = None
        self.audio: AudioAdapters | None = _audio
        if _audio is not None:
            self.speech_input = SpeechInputService(
                _audio.recorder, _audio.asr, self.gateway, _evidence=_evidence
            )
            self.gateway.register_task_resource(self.speech_input)
            self._speech_output = SpeechOutputService(
                _audio.tts, _audio.player, _evidence=_evidence
            )
            self.runtime.registry.register(
                SpeakSkill(self._speech_output, _simulated=_audio.simulated)
            )
        self._exploration_factory: Callable[[int], ExplorationController] | None = (
            self._exploration_builder(_robot, _exploration, speak=_audio is not None)
        )
        self.host: RobotHost = RobotHost(
            self.gateway,
            self._build_agent,
            _speak_replies=_audio is not None,
            _exploration_factory=self._exploration_factory,
        )
        self.events: ActionEventHub = ActionEventHub(self.runtime)

    def _exploration_builder(
        self,
        robot: RobotAdapter,
        config: ExplorationConfig | None,
        *,
        speak: bool,
    ) -> Callable[[int], ExplorationController] | None:
        """底盘带深度避障且具备前进、转向、定位能力时返回探索控制器工厂."""
        required = {
            RobotCapability.MOVE_RELATIVE,
            RobotCapability.TURN_RELATIVE,
            RobotCapability.LOCALIZATION,
        }
        if (
            config is None
            or not isinstance(robot, GuardedRobotAdapter)
            or not required <= robot.capabilities
        ):
            return None
        depth = robot.depth

        def margin(speed: float) -> float:
            """每段前进后留出守护在该车速下所需空隙再加 5 厘米，避免刚走就被拦停."""
            return robot.stop_margin_m(speed) + 0.05

        narrator = model_narrator(self._model, config.narration_chars)

        def build(task_id: int) -> ExplorationController:
            """按任务创建探索控制器."""
            return ExplorationController(
                self.gateway,
                task_id,
                depth,
                narrator,
                self._knowledge,
                config,
                _speak=speak,
                _margin=margin,
            )

        return build

    def _build_agent(self, task_id: int) -> EmbodiedAgent:
        """创建按任务绑定的策略，上下文和工具均不要求课堂会话."""
        explore = (
            (lambda: self.host.request_exploration(task_id))
            if self._exploration_factory is not None
            else None
        )
        return EmbodiedAgent(
            self._model,
            RobotContextBuilder(self.gateway),
            ToolAdapter(
                task_id, self.gateway, _knowledge=self._knowledge, _explore=explore
            ),
            task_id,
            _answer_max_chars=self._answer_max_chars,
        )

    async def start(self) -> None:
        """连接设备并启动 Runtime，再加载常驻语音资源；感知资源由其服务自身管理."""
        await self.runtime.start()
        await self.events.start()
        if self.audio is not None:
            # 识别模型等常驻资源在启动时加载一次，加载失败直接报错，不退回模拟。
            for resource in self.audio.lifecycle:
                await resource.start()

    async def create_task(self, target: str) -> TaskState:
        """创建独立机器人任务，交由宿主登记而非自动开课."""
        task = self.gateway.create_task(target)
        _ = await self.host.start(task.task_id)
        return task

    async def process_next(self) -> RobotReply:
        """消费统一文字或最终语音转写输入，返回可显示或播报的文本."""
        return await self.host.process_next()

    async def cancel_task(self, task_id: int) -> TaskState:
        """统一停止模型、关联动作及注册的语音输入资源."""
        return await self.host.cancel(task_id)

    async def start_recording(
        self,
        task_id: int,
        recording_id: str,
        max_duration_s: float = 15,
        timeout_s: float = 30,
    ) -> None:
        """启动绑定任务的录音，最终转写由服务统一入队."""
        if self.speech_input is None:
            raise RuntimeError("Audio is not configured")
        await self.speech_input.start(task_id, recording_id, max_duration_s, timeout_s)

    async def finish_recording(self, recording_id: str) -> UserInput | None:
        """结束内部录音并等待一次识别结果；调用方不得再次提交转写."""
        if self.speech_input is None:
            raise RuntimeError("Audio is not configured")
        return await self.speech_input.finish(recording_id)

    async def finish_task(self, task_id: int) -> TaskState:
        """由操作者或确定性验收流程确认目标完成，模型文字无权自动确认."""
        return await self.host.finish(task_id)

    async def close(self) -> None:
        """先取消机器人任务，再关闭感知和设备，异常时仍释放设备."""
        try:
            await self.host.close()
        finally:
            try:
                await self._close_audio()
            finally:
                await self._close_devices()

    async def _close_audio(self) -> None:
        """语音输入输出分别清理，任一异常不跳过另一资源；最后释放常驻资源."""
        try:
            if self.speech_input is not None:
                await self.speech_input.close()
        finally:
            try:
                if self._speech_output is not None:
                    await self._speech_output.close()
            finally:
                await self._close_lifecycle()

    async def _close_lifecycle(self) -> None:
        """逐个释放常驻语音资源，单个失败不跳过其余资源，最后重新抛出首个异常."""
        if self.audio is None:
            return
        failure: BaseException | None = None
        for resource in self.audio.lifecycle:
            try:
                await resource.close()
            except Exception as error:
                failure = failure or error
        if failure is not None:
            raise failure

    async def _close_devices(self) -> None:
        """无论感知关闭是否成功，运行时与事件广播均释放."""
        try:
            await self._perception.close()
        finally:
            try:
                await self.runtime.close()
            finally:
                await self.events.close()
