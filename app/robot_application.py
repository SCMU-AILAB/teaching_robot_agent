# app/robot_application.py
"""赛题主应用装配：通用任务、感知、设备、知识与单个决策 Agent."""

from langchain_core.language_models import BaseChatModel

from agent.context import RobotContextBuilder
from agent.embodied_agent import EmbodiedAgent
from agent.robot_host import RobotHost, RobotReply
from agent.tools import ToolAdapter
from app.audio import AudioAdapters
from app.team_gateway import TeamGateway, UserInput
from domain.models import TaskState
from education.knowledge_service import KnowledgeService, build_home_knowledge
from perception.interfaces import PerceptionService
from robot.base import RobotAdapter
from runtime.action_manager import ActionManager
from runtime.events import ActionEventHub
from skills.robot_registry import build_robot_skills
from skills.speak import SpeakSkill
from speech.input import SpeechInputService
from speech.output import SpeechOutputService


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
    ) -> None:
        """显式注入设备、感知与模型，构造期间不连接外部服务."""
        self.runtime: ActionManager = ActionManager(_robot, build_robot_skills(_robot))
        self.gateway: TeamGateway = TeamGateway(self.runtime, _perception)
        self._perception: PerceptionService = _perception
        self._model: BaseChatModel = _model
        self._knowledge: KnowledgeService = _knowledge or build_home_knowledge()
        self.speech_input: SpeechInputService | None = None
        self._speech_output: SpeechOutputService | None = None
        if _audio is not None:
            self.speech_input = SpeechInputService(
                _audio.recorder, _audio.asr, self.gateway
            )
            self.gateway.register_task_resource(self.speech_input)
            self._speech_output = SpeechOutputService(_audio.tts, _audio.player)
            self.runtime.registry.register(
                SpeakSkill(self._speech_output, _simulated=_audio.simulated)
            )
        self.host: RobotHost = RobotHost(
            self.gateway, self._build_agent, _speak_replies=_audio is not None
        )
        self.events: ActionEventHub = ActionEventHub(self.runtime)

    def _build_agent(self, task_id: int) -> EmbodiedAgent:
        """创建按任务绑定的策略，上下文和工具均不要求课堂会话."""
        return EmbodiedAgent(
            self._model,
            RobotContextBuilder(self.gateway),
            ToolAdapter(task_id, self.gateway, _knowledge=self._knowledge),
            task_id,
        )

    async def start(self) -> None:
        """连接设备并启动 Runtime，感知资源由其服务自身管理."""
        await self.runtime.start()
        await self.events.start()

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
        """语音输入输出分别清理，任一异常不跳过另一资源."""
        try:
            if self.speech_input is not None:
                await self.speech_input.close()
        finally:
            if self._speech_output is not None:
                await self._speech_output.close()

    async def _close_devices(self) -> None:
        """无论感知关闭是否成功，运行时与事件广播均释放."""
        try:
            await self._perception.close()
        finally:
            try:
                await self.runtime.close()
            finally:
                await self.events.close()
