# agent/tools.py
"""按任务绑定的核心工具，后续模型适配器调用同一入口."""

from uuid import uuid4

from app.team_gateway import TeamGateway
from domain.education import AnswerEvaluation, KnowledgePoint, TeachingSession
from domain.models import ActionRecord
from domain.services import ObservationRequest, ObservationResult
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
    ) -> None:
        """绑定机器人任务；只有显式注入教育服务时才要求课程会话."""
        if _education is not None:
            _ = _education.get_state(_task_id)
        self._task_id: int = _task_id
        self._gateway: TeamGateway = _gateway
        self._education: EducationService | None = _education
        self._knowledge: KnowledgeService = _knowledge or build_home_knowledge()

    @property
    def teaching_enabled(self) -> bool:
        """报告本任务是否显式装配课程，控制工具暴露范围."""
        return self._education is not None

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
    ) -> ObservationResult:
        """获取观察与证据；调用方必须检查 stale 与 simulated."""
        return await self._gateway.observe(
            ObservationRequest(
                uuid4().hex, self._task_id, question, max_age_s, timeout_s
            )
        )

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
