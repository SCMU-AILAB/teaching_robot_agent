# app/robot_application.py
"""赛题主应用装配：通用任务、感知、设备、知识与单个决策 Agent."""

from langchain_core.language_models import BaseChatModel

from agent.context import RobotContextBuilder
from agent.embodied_agent import EmbodiedAgent
from agent.robot_host import RobotHost, RobotReply
from agent.tools import ToolAdapter
from app.team_gateway import TeamGateway
from domain.models import TaskState
from education.knowledge_service import KnowledgeService, build_home_knowledge
from perception.interfaces import PerceptionService
from robot.base import RobotAdapter
from runtime.action_manager import ActionManager
from skills.robot_registry import build_robot_skills


class RobotApplication:
    """持有服务生命周期；课程不是启动机器人的前置条件."""

    def __init__(
        self,
        _robot: RobotAdapter,
        _perception: PerceptionService,
        _model: BaseChatModel,
        _knowledge: KnowledgeService | None = None,
    ) -> None:
        """显式注入设备、感知与模型，构造期间不连接外部服务."""
        self.runtime: ActionManager = ActionManager(_robot, build_robot_skills(_robot))
        self.gateway: TeamGateway = TeamGateway(self.runtime, _perception)
        self._perception: PerceptionService = _perception
        self._model: BaseChatModel = _model
        self._knowledge: KnowledgeService = _knowledge or build_home_knowledge()
        self.host: RobotHost = RobotHost(self.gateway, self._build_agent)

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

    async def create_task(self, target: str) -> TaskState:
        """创建独立机器人任务，交由宿主登记而非自动开课."""
        task = self.gateway.create_task(target)
        _ = await self.host.start(task.task_id)
        return task

    async def process_next(self) -> RobotReply:
        """消费统一文字或最终语音转写输入，返回可显示或播报的文本."""
        return await self.host.process_next()

    async def close(self) -> None:
        """先取消机器人任务，再关闭感知和设备，异常时仍释放设备."""
        try:
            await self.host.close()
        finally:
            try:
                await self._perception.close()
            finally:
                await self.runtime.close()
