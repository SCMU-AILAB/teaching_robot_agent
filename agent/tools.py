# agent/tools.py
"""按任务绑定的核心工具，后续模型适配器调用同一入口."""

from collections.abc import Callable
from uuid import uuid4

from agent.scene_brief import SceneBrief, build_scene_brief
from app.team_gateway import TeamGateway
from domain.education import AnswerEvaluation, KnowledgePoint, TeachingSession
from domain.models import ActionRecord
from domain.robot import RobotCapability
from domain.services import ObservationRequest
from education.knowledge_service import KnowledgeService, build_home_knowledge
from education.service import EducationService


class ToolAdapter:
    """固定任务归属，避免工具参数让模型切换其他任务."""

    def __init__(
        self,
        _task_id: int,
        _gateway: TeamGateway,
        _education: EducationService | None = None,
        _knowledge: KnowledgeService | None = None,
        _explore: Callable[[], None] | None = None,
    ) -> None:
        """绑定机器人任务；只有显式注入教育服务时才要求课程会话.

        Args:
            _task_id: 任务编号.
            _gateway: 核心网关.
            _education: 可选教育服务，提供时开放课程工具.
            _knowledge: 知识服务，缺省使用家庭知识库.
            _explore: 登记本任务探索请求的回调；None 时不开放探索工具.
        """
        if _education is not None:
            _ = _education.get_state(_task_id)
        self._task_id: int = _task_id
        self._gateway: TeamGateway = _gateway
        self._education: EducationService | None = _education
        self._knowledge: KnowledgeService = _knowledge or build_home_knowledge()
        self._explore: Callable[[], None] | None = _explore

    @property
    def teaching_enabled(self) -> bool:
        """报告本任务是否显式装配课程，控制工具暴露范围."""
        return self._education is not None

    @property
    def exploration_enabled(self) -> bool:
        """报告是否装配了深度避障与探索."""
        return self._explore is not None

    def start_exploration(self, plan: str) -> str:
        """在房间里自由探索：孩子想让你四处转转、看看房间、去探索时调用.

        本条回答播完后在后台开始：边走边避障，离起点 3 米内、最多 5 分钟，发现新东西会停下讲一句；
        孩子说话会打断探索。调用后用一句话告诉孩子你要开始探索了。

        Args:
            plan: 用一句话说明要怎么探索，例如“在房间里四处走走看看”；只用于记录。
        """
        _ = plan
        if self._explore is None:
            raise ValueError("Exploration is not available")
        self._explore()
        return "已安排：本条回答之后开始探索。"

    def _require_education(self) -> EducationService:
        """非课程任务不能意外创建或更新学生成绩."""
        if self._education is None:
            raise ValueError("Teaching mode is not enabled")
        return self._education

    @property
    def task_id(self) -> int:
        """公开绑定任务编号，供装配阶段验证归属."""
        return self._task_id

    async def observe_scene(
        self, question: str, max_age_s: float = 2, timeout_s: float = 30
    ) -> SceneBrief:
        """获取观察与证据，并附带位姿和关联安全资料；调用方必须检查 stale 与 simulated."""
        observation = await self._gateway.observe(
            ObservationRequest(
                uuid4().hex, self._task_id, question, max_age_s, timeout_s
            )
        )
        snapshot = await self._gateway.get_snapshot(self._task_id)
        # 仅当设备声明定位能力时才暴露位姿，避免把默认零值当作真实位置
        pose = (
            snapshot.robot.position
            if RobotCapability.LOCALIZATION in snapshot.capabilities
            else None
        )
        return build_scene_brief(observation, pose, self._knowledge)

    def lookup_knowledge(self, query: str) -> tuple[KnowledgePoint, ...]:
        """查询教学资料，不使用模型记忆冒充外部来源."""
        if self._education is not None:
            return self._education.lookup_knowledge(query)
        return self._knowledge.lookup(query)

    def get_teaching_state(self) -> TeachingSession:
        """读取本任务教学进度."""
        return self._require_education().get_state(self._task_id)

    async def evaluate_answer(
        self, question_id: str, answer: str, submission_id: str
    ) -> AnswerEvaluation:
        """确认任务有效后评价当前问题，保存提交依据."""
        from domain.models import TaskStatus

        snapshot = await self._gateway.get_snapshot(self._task_id)
        if snapshot.task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            raise ValueError("Task is no longer active")
        return self._require_education().evaluate_answer(
            self._task_id, question_id, answer, submission_id
        )

    async def submit_action(
        self, skill: str, arguments: dict[str, object], timeout_s: float = 10
    ) -> ActionRecord:
        """提交正常动作到 Runtime，返回接受记录而非完成声明."""
        return await self._gateway.submit_action(
            self._task_id, skill, arguments, timeout_s
        )

    def get_action_status(self, action_id: int) -> ActionRecord:
        """获取本任务动作的实际状态及完成证据."""
        return self._gateway.get_action_status(self._task_id, action_id)

    async def cancel_action(self, action_id: int) -> ActionRecord:
        """请求取消本任务动作，调用方继续等待停止确认."""
        return await self._gateway.cancel_action(self._task_id, action_id)

    async def wait_for_action(self, action_id: int) -> ActionRecord:
        """供宿主决策循环等待动作终态，不注册为模型轮询工具."""
        return await self._gateway.wait_for_action(self._task_id, action_id)
