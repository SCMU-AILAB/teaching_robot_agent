# api/service.py
"""HTTP API 的服务层：把核心任务、消息、动作、观察与录音映射为合同资源与事件.

与 Web 框架无关；所有状态只在事件循环线程中修改，同步段内生成的快照与事件游标一致。
"""

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

import httpx

from api.errors import ApiError, ErrorCode, error_code_of
from api.events import EventSubscriber, TaskEventLog, parse_cursor
from api.preview import CameraPreview
from api.schemas import (
    DEFAULT_USER_TARGET,
    CreateMessageRequest,
    CreateRecordingRequest,
    CreateTaskRequest,
    JsonObject,
    MessageRecord,
    RecordingRecord,
    action_payload,
    dump_json,
    observation_payload,
    resource_error,
    task_payload,
)
from app.robot_application import RobotApplication
from app.team_gateway import UserInput
from domain.models import TaskStatus
from domain.services import ObservationResult, ServiceError
from storage.evidence import EvidenceStore

logger = logging.getLogger(__name__)

type ServiceState = Literal["ready", "unavailable", "not_configured"]

_ACTIVE = frozenset({TaskStatus.PENDING, TaskStatus.RUNNING})
_TERMINAL = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})
_MESSAGE_LIMIT = 100
_ACTION_LIMIT = 200
_REQUEST_RETENTION = 4096


@dataclass(frozen=True)
class ServiceStates:
    """SystemResponse.services；由入口按装配结果给出，首版不做主动探测."""

    agent: ServiceState
    vlm: ServiceState
    asr: ServiceState
    tts: ServiceState


@dataclass
class _TaskView:
    """单个任务对外可见的最新记录与事件日志."""

    log: TaskEventLog
    task: JsonObject
    messages: dict[str, MessageRecord] = field(default_factory=dict)
    actions: dict[int, JsonObject] = field(default_factory=dict)
    observation: ObservationResult | None = None
    recordings: dict[str, RecordingRecord] = field(default_factory=dict)
    # 机器人回答按用户消息编号登记，避免重复生成。
    replies: dict[str, str] = field(default_factory=dict)
    # 本任务观察事件公开过的画面，任务记录清理时一并撤销公开。
    evidence_ids: set[str] = field(default_factory=set)


@dataclass
class _RequestEntry:
    """创建类请求的去重记录."""

    fingerprint: str
    future: asyncio.Future[JsonObject]


class RobotApiService:
    """实现 API_CONTRACT.md v1.0 的业务语义；同时只有一个活动任务."""

    def __init__(
        self,
        _app: RobotApplication,
        *,
        _services: ServiceStates,
        _simulated: bool,
        _preview: CameraPreview | None = None,
        _evidence: EvidenceStore | None = None,
        _camera_error: str = "未配置摄像头",
        _max_tasks: int = 256,
    ) -> None:
        """注入已装配的应用；构造期间不启动后台任务.

        Args:
            _app: 机器人主应用，须先于本服务启动、晚于本服务关闭.
            _services: 各外部服务的配置状态.
            _simulated: 底盘或感知是否为模拟实现.
            _preview: 摄像头预览；没有真实相机时为 None.
            _evidence: 观察画面所在的证据存储.
            _camera_error: 没有预览时 SystemResponse.camera.error 的说明.
            _max_tasks: 内存中保留的任务记录上限，超出时清理最早的已结束任务.
        """
        self.instance_id: str = uuid4().hex
        self._app: RobotApplication = _app
        self._services: ServiceStates = _services
        self._simulated: bool = _simulated
        self._preview: CameraPreview | None = _preview
        self._evidence: EvidenceStore | None = _evidence
        self._camera_error: str = _camera_error
        self._max_tasks: int = _max_tasks
        self._views: OrderedDict[int, _TaskView] = OrderedDict()
        self._requests: OrderedDict[tuple[str, str], _RequestEntry] = OrderedDict()
        self._processing: dict[int, str] = {}
        self._linked_speech: set[int] = set()
        # 只有经观察事件公开过的证据可以通过 HTTP 读取，录音等其他媒体不对外。
        self._exposed_evidence: set[str] = set()
        self._create_lock: asyncio.Lock = asyncio.Lock()
        self._recording_lock: asyncio.Lock = asyncio.Lock()
        self._workers: list[asyncio.Task[None]] = []
        self._background: set[asyncio.Task[object]] = set()
        self._started: bool = False

    # ========== 生命周期 ==========

    async def start(self) -> None:
        """登记核心回调并启动输入消费者与动作事件转发."""
        if self._started:
            return
        self._started = True
        self._app.gateway.add_terminal_listener(self._on_task_terminal)
        self._app.gateway.add_observation_listener(self._on_observation)
        self._workers = [
            asyncio.create_task(self._consume(), name="api-input-consumer"),
            asyncio.create_task(self._pump_actions(), name="api-action-events"),
        ]

    async def close(self) -> None:
        """停止后台任务并让所有事件订阅重置；机器人任务由应用关闭时取消."""
        tasks = [*self._workers, *self._background]
        for task in tasks:
            _ = task.cancel()
        _ = await asyncio.gather(*tasks, return_exceptions=True)
        if self._preview is not None:
            await self._preview.close()
        for view in self._views.values():
            view.log.close("server_closed")

    def _spawn(self, coroutine: Awaitable[object], name: str) -> None:
        """登记后台作业，关闭时统一取消，异常写日志."""

        async def run() -> object:
            try:
                return await coroutine
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[RobotApiService._spawn] 后台作业失败 name=%s", name)
                return None

        task: asyncio.Task[object] = asyncio.create_task(run(), name=name)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # ========== 系统 ==========

    async def system(self) -> JsonObject:
        """SystemResponse；只读内部状态."""
        robot = await self._app.runtime.robot.get_state()
        if self._preview is not None:
            camera = self._preview.status()
            camera_payload: JsonObject = {
                "available": camera.available,
                "last_frame_at": camera.last_frame_at,
                "error": camera.error,
            }
        else:
            camera_payload = {
                "available": False,
                "last_frame_at": None,
                "error": self._camera_error,
            }
        playing = any(
            record.raw_request.skill_name == "speak" and not record.status.is_terminal
            for record in self._app.runtime.list_actions()
        )
        recording = any(
            item.active
            for view in self._views.values()
            for item in view.recordings.values()
        )
        return {
            "instance_id": self.instance_id,
            "active_task_id": self._active_task_id(),
            "simulated": self._simulated,
            "runtime_blocked_reason": self._app.runtime.blocked_reason,
            "robot": {
                "connected": robot.is_connected,
                "moving": robot.is_moving,
                "x": robot.position.x,
                "y": robot.position.y,
                "yaw": robot.position.yaw,
                "updated_at": robot.updated_at,
            },
            "camera": camera_payload,
            "audio": {
                "available": self._app.speech_input is not None,
                "recording": recording,
                "playing": playing,
            },
            "services": {
                "agent": self._services.agent,
                "vlm": self._services.vlm,
                "asr": self._services.asr,
                "tts": self._services.tts,
            },
        }

    # ========== 任务 ==========

    async def create_task(self, request: CreateTaskRequest) -> JsonObject:
        """POST /tasks."""
        return await self._idempotent(
            ("/tasks", request.client_request_id),
            dump_json(request.model_dump()),
            lambda: self._create_task(request.user_target or DEFAULT_USER_TARGET),
        )

    async def _create_task(self, user_target: str) -> JsonObject:
        """检查执行层与活动任务后创建；创建过程加锁，避免并发出现两个活动任务."""
        async with self._create_lock:
            if self._app.runtime.blocked_reason is not None:
                raise ApiError(
                    ErrorCode.RUNTIME_BLOCKED, "设备停止状态无法确认，需人工处理后重启"
                )
            if self._active_task_id() is not None:
                raise ApiError(ErrorCode.INVALID_STATE, "已有未结束的任务")
            task = self._app.gateway.create_task(user_target)
            self._views[task.task_id] = _TaskView(
                TaskEventLog(self.instance_id, task.task_id), task_payload(task)
            )
            self._views[task.task_id].log.publish("task.updated", task_payload(task))
            self._evict_tasks()
            try:
                _ = await self._app.host.start(task.task_id)
            except Exception:
                logger.exception(
                    "[RobotApiService._create_task] 宿主登记失败 task_id=%s",
                    task.task_id,
                )
                _ = await self._app.cancel_task(task.task_id)
                raise
            self._refresh_task(task.task_id)
            logger.info(
                "[RobotApiService._create_task] 任务已创建 task_id=%s", task.task_id
            )
            return self._views[task.task_id].task

    def snapshot(self, task_id: int) -> JsonObject:
        """TaskSnapshotResponse；内容与游标在同一同步段内生成."""
        view = self._view(task_id)
        self._refresh_task(task_id)
        messages = list(view.messages.values())
        actions = [view.actions[key] for key in sorted(view.actions)]
        active = next((item for item in view.recordings.values() if item.active), None)
        observation = (
            self._observation_payload(view.observation)
            if view.observation is not None
            else None
        )
        return {
            "instance_id": self.instance_id,
            "task": view.task,
            "messages": [item.payload() for item in messages[-_MESSAGE_LIMIT:]],
            "actions": actions[-_ACTION_LIMIT:],
            "latest_observation": observation,
            "active_recording": active.payload() if active is not None else None,
            "truncated": {
                "messages": len(messages) > _MESSAGE_LIMIT,
                "actions": len(actions) > _ACTION_LIMIT,
            },
            "event_cursor": view.log.cursor,
        }

    async def update_task(
        self, task_id: int, status: Literal["cancelling", "completed"]
    ) -> tuple[int, JsonObject]:
        """PATCH /tasks/{task_id}；返回 HTTP 状态码与 TaskResponse."""
        view = self._view(task_id)
        task = self._app.gateway.get_task(task_id)
        if status == "cancelling":
            if task.status in _TERMINAL:
                return 200, view.task
            # 取消在后台完成，HTTP 连接断开不撤销；终态经 task.updated 发布。
            self._spawn(self._app.cancel_task(task_id), f"api-cancel-{task_id}")
            await asyncio.sleep(0)
            self._refresh_task(task_id)
            return 202, view.task
        if task.status == TaskStatus.COMPLETED:
            return 200, view.task
        if task.status not in _ACTIVE:
            raise ApiError(ErrorCode.INVALID_STATE, "任务已结束或正在停止")
        self._require_idle(view)
        try:
            _ = await self._app.finish_task(task_id)
        except (ValueError, RuntimeError) as error:
            raise ApiError(
                ErrorCode.INVALID_STATE, "任务仍有动作或语音处理未结束"
            ) from error
        self._refresh_task(task_id)
        return 200, view.task

    # ========== 消息 ==========

    async def post_message(
        self, task_id: int, request: CreateMessageRequest
    ) -> JsonObject:
        """POST /tasks/{task_id}/messages."""
        return await self._idempotent(
            (f"/tasks/{task_id}/messages", request.client_request_id),
            dump_json(request.model_dump()),
            lambda: self._post_message(task_id, request.text),
        )

    async def _post_message(self, task_id: int, text: str) -> JsonObject:
        """检查任务与忙碌状态后入队，由唯一消费者处理."""
        view = self._view(task_id)
        self._require_active(task_id)
        self._require_idle(view)
        try:
            item = self._app.gateway.submit_text(task_id, text)
        except ValueError as error:
            raise ApiError(ErrorCode.INVALID_STATE, "任务已结束") from error
        return self._ensure_user_message(item).payload()

    def _ensure_user_message(self, item: UserInput) -> MessageRecord:
        """按输入编号登记用户消息；文字入口与语音识别完成都会调用，重复调用安全."""
        view = self._view(item.task_id)
        message = view.messages.get(item.input_id)
        if message is None:
            message = MessageRecord(
                item.input_id,
                item.task_id,
                "user",
                item.text,
                "speech" if item.source == "speech" else "text",
                "accepted",
                time.time(),
            )
            view.messages[item.input_id] = message
            view.log.publish("message.updated", message.payload())
        return message

    def _update_message(self, message: MessageRecord, **changes: object) -> None:
        """修改消息字段并发布 message.updated."""
        for name, value in changes.items():
            setattr(message, name, value)
        self._view(message.task_id).log.publish("message.updated", message.payload())

    async def _consume(self) -> None:
        """唯一输入消费者：逐条处理文字与语音消息，单条失败不终止循环."""
        while True:
            item = await self._app.gateway.next_input(include_terminal=True)
            if item.task_id not in self._views:
                continue
            message = self._ensure_user_message(item)
            if self._app.gateway.get_task(item.task_id).status not in _ACTIVE:
                self._update_message(message, status="cancelled")
                continue
            self._update_message(message, status="processing")
            self._processing[item.task_id] = message.message_id
            try:
                reply = await self._app.host.handle(item)
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
                # 任务停止取消了处理协程，不是本消费者被取消。
                self._update_message(message, status="cancelled")
            except ValueError:
                self._update_message(
                    message,
                    status="rejected",
                    error=resource_error(ErrorCode.INVALID_STATE, "任务已停止，未执行"),
                )
            except ServiceError as error:
                code = error_code_of(error.code)
                self._update_message(
                    message,
                    status="rejected",
                    error=resource_error(code, "模型或设备服务处理失败"),
                )
            except (httpx.TransportError, ConnectionError, TimeoutError) as error:
                logger.warning(
                    "[RobotApiService._consume] 模型服务不可用 task_id=%s error=%r",
                    item.task_id,
                    error,
                )
                self._update_message(
                    message,
                    status="rejected",
                    error=resource_error(
                        ErrorCode.UNAVAILABLE, "模型服务不可用，任务已停止"
                    ),
                )
            except Exception:
                logger.exception(
                    "[RobotApiService._consume] 消息处理失败 task_id=%s", item.task_id
                )
                self._update_message(
                    message,
                    status="rejected",
                    error=resource_error(ErrorCode.INTERNAL, "处理失败，任务已停止"),
                )
            else:
                speech_ids = [
                    action_id
                    for action_id in reply.action_ids
                    if self._app.runtime.get_action(action_id).raw_request.skill_name
                    == "speak"
                ]
                self._record_reply(
                    message, reply.text, speech_ids[-1] if speech_ids else None
                )
                self._update_message(message, status="answered")
            finally:
                _ = self._processing.pop(item.task_id, None)
                self._refresh_task(item.task_id)

    def _record_reply(
        self, message: MessageRecord, text: str, speech_action_id: int | None
    ) -> None:
        """登记或补全机器人回答；播报开始时可能已由动作事件先行生成."""
        view = self._view(message.task_id)
        if speech_action_id is not None:
            self._linked_speech.add(speech_action_id)
        reply_id = view.replies.get(message.message_id)
        if reply_id is None:
            reply = MessageRecord(
                uuid4().hex,
                message.task_id,
                "assistant",
                text,
                "agent",
                "final",
                time.time(),
                reply_to=message.message_id,
                speech_action_id=speech_action_id,
            )
            view.messages[reply.message_id] = reply
            view.replies[message.message_id] = reply.message_id
            view.log.publish("message.updated", reply.payload())
            return
        reply = view.messages[reply_id]
        if reply.text != text or (
            speech_action_id is not None and reply.speech_action_id != speech_action_id
        ):
            self._update_message(
                reply,
                text=text,
                speech_action_id=speech_action_id or reply.speech_action_id,
            )

    # ========== 录音 ==========

    async def start_recording(
        self, task_id: int, request: CreateRecordingRequest
    ) -> JsonObject:
        """POST /tasks/{task_id}/recordings."""
        return await self._idempotent(
            (f"/tasks/{task_id}/recordings", request.client_request_id),
            dump_json(request.model_dump()),
            lambda: self._start_recording(task_id, request.max_duration_s),
        )

    async def _start_recording(self, task_id: int, max_duration_s: float) -> JsonObject:
        """开始录音并启动结果监听；录音与消息处理、播报互斥."""
        view = self._view(task_id)
        speech = self._app.speech_input
        audio = self._app.audio
        if speech is None or audio is None:
            raise ApiError(ErrorCode.UNAVAILABLE, "未配置麦克风与语音识别")
        async with self._recording_lock:
            self._require_active(task_id)
            self._require_idle(view)
            recording_id = uuid4().hex
            try:
                await self._app.start_recording(
                    task_id, recording_id, max_duration_s, audio.timeout_s
                )
            except ValueError as error:
                raise ApiError(ErrorCode.INVALID_STATE, "任务已结束") from error
            except RuntimeError as error:
                # 音频租约被播放占用时报 busy，其余视为设备不可用。
                if "busy" in str(error).lower():
                    raise ApiError(ErrorCode.INVALID_STATE, "麦克风正被占用") from error
                raise ApiError(ErrorCode.UNAVAILABLE, "麦克风不可用") from error
            record = RecordingRecord(recording_id, task_id, time.time())
            view.recordings[recording_id] = record
            view.log.publish("recording.updated", record.payload())
            self._spawn(
                self._watch_recording(record, max_duration_s),
                f"api-recording-{recording_id}",
            )
            return record.payload()

    async def _watch_recording(
        self, record: RecordingRecord, max_duration_s: float
    ) -> None:
        """等待录音与识别结束，把结果写入录音记录；非空转写同时登记为用户消息."""
        speech = self._app.speech_input
        if speech is None:
            return
        timer = asyncio.create_task(self._mark_auto_finish(record, max_duration_s))
        try:
            item = await speech.wait_finished(record.recording_id)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            self._finish_recording(record, "cancelled")
        except ServiceError as error:
            self._finish_recording(
                record,
                "failed",
                error=resource_error(error_code_of(error.code), "语音识别失败"),
            )
        except Exception as error:
            logger.warning(
                "[RobotApiService._watch_recording] 录音或识别失败 error=%r", error
            )
            self._finish_recording(
                record,
                "failed",
                error=resource_error(ErrorCode.UNAVAILABLE, "录音或语音识别失败"),
            )
        else:
            message_id = (
                self._ensure_user_message(item).message_id if item is not None else None
            )
            self._finish_recording(
                record,
                "completed",
                transcript=item.text if item is not None else "",
                message_id=message_id,
            )
        finally:
            _ = timer.cancel()

    async def _mark_auto_finish(
        self, record: RecordingRecord, max_duration_s: float
    ) -> None:
        """录音器到达最长时长会自行结束，这里同步对外状态为识别中."""
        await asyncio.sleep(max_duration_s)
        if record.status == "recording":
            record.status = "transcribing"
            self._view(record.task_id).log.publish(
                "recording.updated", record.payload()
            )

    def _finish_recording(
        self,
        record: RecordingRecord,
        status: Literal["completed", "failed", "cancelled"],
        *,
        transcript: str | None = None,
        message_id: str | None = None,
        error: JsonObject | None = None,
    ) -> None:
        """录音进入终态并发布；重复进入同一终态不重复发布."""
        if not record.active:
            return
        record.status = status
        record.ended_at = time.time()
        record.transcript = transcript
        record.message_id = message_id
        record.error = error
        self._view(record.task_id).log.publish("recording.updated", record.payload())

    async def update_recording(
        self,
        task_id: int,
        recording_id: str,
        status: Literal["transcribing", "cancelled"],
    ) -> tuple[int, JsonObject]:
        """PATCH /tasks/{task_id}/recordings/{recording_id}."""
        view = self._view(task_id)
        record = view.recordings.get(recording_id)
        speech = self._app.speech_input
        if record is None or speech is None:
            raise ApiError(ErrorCode.NOT_FOUND, "录音不存在")
        if record.status == status:
            return 200, record.payload()
        if not record.active:
            raise ApiError(ErrorCode.INVALID_STATE, "录音已结束")
        if status == "transcribing":
            if record.status != "recording":
                return 200, record.payload()
            record.status = "transcribing"
            view.log.publish("recording.updated", record.payload())
            # 结束录音在后台进行；识别结果由监听作业写入。
            self._spawn(speech.finish(recording_id), f"api-finish-{recording_id}")
            return 202, record.payload()
        await asyncio.shield(speech.cancel(recording_id))
        self._finish_recording(record, "cancelled")
        return 200, record.payload()

    # ========== 事件、观察与媒体 ==========

    def subscribe(
        self, task_id: int, cursor: str | None
    ) -> tuple[TaskEventLog, EventSubscriber]:
        """校验游标并订阅；游标失效时订阅直接返回 stream.reset.

        Raises:
            ApiError: 40001 缺少或格式错误的游标；40401 任务不存在.
        """
        view = self._view(task_id)
        if cursor is None or not cursor.strip():
            raise ApiError(ErrorCode.INVALID_ARGUMENT, "缺少事件游标，请先读取任务快照")
        instance_id, sequence = parse_cursor(cursor)
        return view.log, view.log.subscribe(instance_id, sequence)

    async def read_evidence(self, evidence_id: str) -> tuple[bytes, str]:
        """GET /evidence/{evidence_id}；只返回已经对外公开且尚未清理的画面."""
        if self._evidence is None or evidence_id not in self._exposed_evidence:
            raise ApiError(ErrorCode.NOT_FOUND, "证据不存在")
        try:
            reference = self._evidence.get(evidence_id)
            content = await self._evidence.read(evidence_id)
        except KeyError:
            self._exposed_evidence.discard(evidence_id)
            raise ApiError(ErrorCode.NOT_FOUND, "证据不存在或已清理") from None
        return content, reference.media_type

    async def camera_snapshot(self, max_width: int | None) -> bytes:
        """GET /camera/snapshot."""
        if self._preview is None:
            raise ApiError(ErrorCode.UNAVAILABLE, self._camera_error)
        return await self._preview.snapshot(max_width)

    def _on_observation(self, result: ObservationResult) -> None:
        """核心登记观察后的同步回调."""
        view = self._views.get(result.task_id)
        if view is None:
            return
        view.observation = result
        view.evidence_ids.add(result.frame.evidence_id)
        self._exposed_evidence.add(result.frame.evidence_id)
        view.log.publish("observation.created", self._observation_payload(result))

    def _observation_payload(self, result: ObservationResult) -> JsonObject:
        """画面已清理时 frame_evidence_id 为 null."""
        available = False
        if self._evidence is not None:
            try:
                _ = self._evidence.get(result.frame.evidence_id)
                available = True
            except KeyError:
                available = False
        return observation_payload(result, frame_available=available)

    def _on_task_terminal(self, task_id: int) -> None:
        """任务终态回调：发布 task.updated."""
        if task_id in self._views:
            self._refresh_task(task_id)

    async def _pump_actions(self) -> None:
        """转发动作状态事件；订阅溢出后重新订阅并按最新记录补齐."""
        while True:
            try:
                async with self._app.events.subscribe() as subscription:
                    self._resync_actions()
                    while True:
                        event = await subscription.next_event()
                        self._on_action(event.action_id)
            except RuntimeError as error:
                if "closed" in str(error).lower():
                    return
                logger.warning(
                    "[RobotApiService._pump_actions] 动作事件积压，重新同步 error=%s",
                    error,
                )
            except EOFError:
                return

    def _resync_actions(self) -> None:
        """按运行时当前记录补齐全部任务的动作."""
        for record in self._app.runtime.list_actions():
            self._on_action(record.action_id)

    def _on_action(self, action_id: int) -> None:
        """动作变化：发布最新记录；播报开始时先行生成机器人回答."""
        try:
            record = self._app.runtime.get_action(action_id)
        except KeyError:
            # 积压事件对应的旧动作记录可能已被清理。
            return
        task_id = record.raw_request.task_id
        view = self._views.get(task_id)
        if view is None:
            return
        payload = action_payload(record)
        if view.actions.get(action_id) != payload:
            view.actions[action_id] = payload
            view.log.publish("action.updated", payload)
        text = record.raw_request.args.get("text")
        message_id = self._processing.get(task_id)
        if (
            record.raw_request.skill_name == "speak"
            and action_id not in self._linked_speech
            and message_id is not None
            and isinstance(text, str)
        ):
            self._record_reply(view.messages[message_id], text, action_id)
        self._refresh_task(task_id)

    # ========== 内部辅助 ==========

    def _view(self, task_id: int) -> _TaskView:
        """查找任务记录.

        Raises:
            ApiError: 40401.
        """
        view = self._views.get(task_id)
        if view is None:
            raise ApiError(ErrorCode.NOT_FOUND, "任务不存在")
        return view

    def _refresh_task(self, task_id: int) -> None:
        """任务记录变化时发布 task.updated."""
        view = self._views.get(task_id)
        if view is None:
            return
        payload = task_payload(self._app.gateway.get_task(task_id))
        if payload != view.task:
            view.task = payload
            view.log.publish("task.updated", payload)

    def _active_task_id(self) -> int | None:
        """未结束的任务编号."""
        for task_id in self._views:
            if self._app.gateway.get_task(task_id).status not in _TERMINAL:
                return task_id
        return None

    def _require_active(self, task_id: int) -> None:
        """任务仍接受输入."""
        if self._app.gateway.get_task(task_id).status not in _ACTIVE:
            raise ApiError(ErrorCode.INVALID_STATE, "任务已结束或正在停止")

    @staticmethod
    def _require_idle(view: _TaskView) -> None:
        """没有处理中的消息，也没有进行中的录音."""
        if any(item.pending for item in view.messages.values()):
            raise ApiError(ErrorCode.INVALID_STATE, "上一条消息仍在处理")
        if any(item.active for item in view.recordings.values()):
            raise ApiError(ErrorCode.INVALID_STATE, "正在录音或识别")

    async def _idempotent(
        self,
        key: tuple[str, str],
        fingerprint: str,
        factory: Callable[[], Awaitable[JsonObject]],
    ) -> JsonObject:
        """按“路径 + client_request_id”去重；失败的请求不保留，可用原编号重试.

        Raises:
            ApiError: 40009，同一编号用于内容不同的请求.
        """
        entry = self._requests.get(key)
        if entry is not None:
            if entry.fingerprint != fingerprint:
                raise ApiError(
                    ErrorCode.REQUEST_ID_CONFLICT, "client_request_id 已用于其他内容"
                )
            return await asyncio.shield(entry.future)
        future: asyncio.Future[JsonObject] = asyncio.get_running_loop().create_future()
        self._requests[key] = _RequestEntry(fingerprint, future)
        try:
            result = await factory()
        except BaseException as error:
            _ = self._requests.pop(key, None)
            if isinstance(error, asyncio.CancelledError):
                _ = future.cancel()
            else:
                future.set_exception(error)
                # 没有并发重复请求时也标记异常已读取，避免事件循环告警。
                _ = future.exception()
            raise
        future.set_result(result)
        while len(self._requests) > _REQUEST_RETENTION:
            _ = self._requests.popitem(last=False)
        return result

    def _evict_tasks(self) -> None:
        """超出保留上限时清理最早的已结束任务记录."""
        for task_id in list(self._views):
            if len(self._views) <= self._max_tasks:
                return
            if self._app.gateway.get_task(task_id).status in _TERMINAL:
                view = self._views.pop(task_id)
                view.log.close("task_evicted")
                self._linked_speech.difference_update(view.actions)
                # 其他任务可能经缓存共享同一帧，仍被引用的保持公开。
                shared = set[str]().union(
                    *(item.evidence_ids for item in self._views.values())
                )
                self._exposed_evidence.difference_update(view.evidence_ids - shared)
