# api/http.py
"""FastAPI 路由：只做参数解析与响应包装，业务语义在 RobotApiService."""

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated, cast, override

from fastapi import FastAPI, Header, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.errors import ApiError, ErrorCode
from api.events import EventSubscriber, StreamResetError, TaskEventLog
from api.schemas import (
    CreateMessageRequest,
    CreateRecordingRequest,
    CreateTaskRequest,
    UpdateRecordingRequest,
    UpdateTaskRequest,
    dump_json,
    validation_message,
)
from api.service import RobotApiService

logger = logging.getLogger(__name__)

PREFIX = "/api/v1"
HEARTBEAT_S = 15.0

TaskId = Annotated[int, Path(gt=0, le=2**53)]
OpaqueId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


class ContractJSONResponse(JSONResponse):
    """UTF-8 紧凑 JSON，拒绝 NaN 与 Infinity."""

    media_type: str | None = "application/json"

    @override
    def render(self, content: object) -> bytes:
        """使用合同统一的 JSON 编码."""
        return dump_json(content).encode()


def success(data: object, status_code: int = 200) -> ContractJSONResponse:
    """统一成功响应."""
    return ContractJSONResponse(
        {"code": 0, "message": "success", "data": data}, status_code
    )


def failure(code: ErrorCode, message: str) -> ContractJSONResponse:
    """统一失败响应，HTTP 状态码由错误码决定."""
    return ContractJSONResponse(
        {"code": int(code), "message": message, "data": None}, code.http_status
    )


class ServiceSlot:
    """路由使用的服务实例；入口在生命周期内装配，测试直接写入."""

    def __init__(self, _service: RobotApiService | None = None) -> None:
        """初始化为空，装配完成前请求返回 50002."""
        self.service: RobotApiService | None = _service

    def require(self) -> RobotApiService:
        """取已装配的服务.

        Raises:
            ApiError: 50002，后端尚未完成启动.
        """
        if self.service is None:
            raise ApiError(ErrorCode.UNAVAILABLE, "机器人后端尚未就绪")
        return self.service


async def _stream(
    log: TaskEventLog, subscriber: EventSubscriber, heartbeat_s: float
) -> AsyncIterator[str]:
    """逐条输出 SSE；空闲时发送注释心跳，订阅失效时发送 stream.reset 后结束."""
    try:
        while True:
            try:
                event = await subscriber.next(heartbeat_s)
            except StreamResetError as reset:
                yield log.reset_event(reset.reason).to_sse()
                return
            yield ": heartbeat\n\n" if event is None else event.to_sse()
    finally:
        log.unsubscribe(subscriber)


def build_http_app(
    slot: ServiceSlot,
    *,
    lifespan: Callable[[ServiceSlot], AbstractAsyncContextManager[None]] | None = None,
    heartbeat_s: float = HEARTBEAT_S,
) -> FastAPI:
    """创建路由.

    Args:
        slot: 服务实例槽位.
        lifespan: 入口提供的生命周期，负责装配并写入槽位；测试时为 None.
        heartbeat_s: SSE 心跳间隔，秒.

    Returns:
        FastAPI 应用.
    """

    @asynccontextmanager
    async def run_lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        """把框架生命周期转交给入口."""
        if lifespan is None:
            yield
            return
        async with lifespan(slot):
            yield

    app = FastAPI(
        title="家庭学习陪伴与安全教育机器人 API",
        version="1.0",
        docs_url=f"{PREFIX}/docs",
        openapi_url=f"{PREFIX}/openapi.json",
        redoc_url=None,
        lifespan=run_lifespan,
    )
    _register_errors(app)

    @app.get(f"{PREFIX}/system")
    async def get_system() -> Response:
        """设备与服务状态."""
        return success(await slot.require().system())

    @app.post(f"{PREFIX}/tasks", status_code=201)
    async def create_task(body: CreateTaskRequest) -> Response:
        """开始一次对话任务."""
        return success(await slot.require().create_task(body), 201)

    @app.get(f"{PREFIX}/tasks/{{task_id}}")
    async def get_task(task_id: TaskId) -> Response:
        """任务快照."""
        return success(slot.require().snapshot(task_id))

    @app.patch(f"{PREFIX}/tasks/{{task_id}}")
    async def update_task(task_id: TaskId, body: UpdateTaskRequest) -> Response:
        """停止任务或确认完成."""
        status, data = await slot.require().update_task(task_id, body.status)
        return success(data, status)

    @app.post(f"{PREFIX}/tasks/{{task_id}}/messages", status_code=202)
    async def post_message(task_id: TaskId, body: CreateMessageRequest) -> Response:
        """发送一句文字."""
        return success(await slot.require().post_message(task_id, body), 202)

    @app.post(f"{PREFIX}/tasks/{{task_id}}/recordings", status_code=201)
    async def start_recording(
        task_id: TaskId, body: CreateRecordingRequest
    ) -> Response:
        """开始录音."""
        return success(await slot.require().start_recording(task_id, body), 201)

    @app.patch(f"{PREFIX}/tasks/{{task_id}}/recordings/{{recording_id}}")
    async def update_recording(
        task_id: TaskId,
        recording_id: OpaqueId,
        body: UpdateRecordingRequest,
    ) -> Response:
        """结束录音并识别，或放弃录音."""
        status, data = await slot.require().update_recording(
            task_id, recording_id, body.status
        )
        return success(data, status)

    @app.get(f"{PREFIX}/tasks/{{task_id}}/events")
    async def get_events(
        task_id: TaskId,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        after: Annotated[str | None, Query(max_length=64)] = None,
    ) -> Response:
        """任务事件流；请求头优先于查询参数."""
        log, subscriber = slot.require().subscribe(
            task_id, last_event_id if last_event_id is not None else after
        )
        return StreamingResponse(
            _stream(log, subscriber, heartbeat_s),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(f"{PREFIX}/camera/snapshot")
    async def camera_snapshot(
        max_width: Annotated[int | None, Query(ge=160, le=1920)] = None,
    ) -> Response:
        """摄像头最近一帧 JPEG."""
        content = await slot.require().camera_snapshot(max_width)
        return Response(
            content, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )

    @app.get(f"{PREFIX}/evidence/{{evidence_id}}")
    async def get_evidence(evidence_id: OpaqueId) -> Response:
        """已公开的观察画面."""
        content, media_type = await slot.require().read_evidence(evidence_id)
        return Response(
            content, media_type=media_type, headers={"Cache-Control": "no-store"}
        )

    return app


def _register_errors(app: FastAPI) -> None:
    """所有失败都转换为统一响应，不返回堆栈、路径或凭据."""

    async def on_api_error(_request: Request, error: Exception) -> Response:
        """业务错误."""
        if not isinstance(error, ApiError):
            raise error
        return failure(error.code, error.message)

    async def on_validation(_request: Request, error: Exception) -> Response:
        """路径、查询与请求体校验失败."""
        if not isinstance(error, RequestValidationError):
            raise error
        errors = cast(list[dict[str, object]], list(error.errors()))
        return failure(ErrorCode.INVALID_ARGUMENT, validation_message(errors))

    async def on_http(_request: Request, error: Exception) -> Response:
        """路由不存在或方法不支持."""
        if isinstance(error, StarletteHTTPException) and error.status_code == 404:
            return failure(ErrorCode.NOT_FOUND, "接口不存在")
        return failure(ErrorCode.INVALID_ARGUMENT, "不支持的请求")

    async def on_unexpected(_request: Request, error: Exception) -> Response:
        """未分类错误只写日志."""
        logger.error("[build_http_app.on_unexpected] 未处理异常", exc_info=error)
        return failure(ErrorCode.INTERNAL, "服务内部错误")

    app.add_exception_handler(ApiError, on_api_error)
    app.add_exception_handler(RequestValidationError, on_validation)
    app.add_exception_handler(StarletteHTTPException, on_http)
    app.add_exception_handler(Exception, on_unexpected)
