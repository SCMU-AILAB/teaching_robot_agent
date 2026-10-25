# tests/test_http_api.py
"""按 API_CONTRACT.md v1.0 验证 HTTP 接口、事件流与服务层语义."""

import asyncio
import io
import json
import tempfile
import unittest
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from PIL import Image
from starlette.types import Message, Receive, Scope, Send

from api.events import StreamResetError, TaskEventLog
from api.http import ServiceSlot, build_http_app
from api.preview import CameraPreview
from api.schemas import MessageRecord
from api.service import RobotApiService, ServiceStates
from app.audio import build_simulated_audio
from app.robot_application import RobotApplication
from domain.models import TaskStatus
from perception.camera import FileCamera
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter
from storage.evidence import EvidenceStore

type Headers = tuple[tuple[str, str], ...]


def dig(value: object, *path: str | int) -> object:
    """按键或下标逐层读取 JSON，类型不符直接断言失败."""
    for key in path:
        if isinstance(key, str):
            assert isinstance(value, dict), value
            value = cast(dict[str, object], value)[key]
        else:
            assert isinstance(value, list), value
            value = cast(list[object], value)[key]
    return value


def items(value: object, *path: str | int) -> list[object]:
    """读取 JSON 列表."""
    found = dig(value, *path)
    assert isinstance(found, list)
    return cast(list[object], found)


def _scope(method: str, path: str, headers: Headers) -> Scope:
    """构造最小 HTTP 请求范围."""
    raw_path, _, query = path.partition("?")
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": raw_path,
        "raw_path": raw_path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            *((key.lower().encode(), value.encode()) for key, value in headers),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }


@dataclass
class Reply:
    """一次 HTTP 响应."""

    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def json(self) -> object:
        """解析后的统一响应."""
        return cast(object, json.loads(self.body))

    @property
    def data(self) -> object:
        """成功响应的 data."""
        assert dig(self.json, "code") == 0, self.body
        return dig(self.json, "data")

    @property
    def code(self) -> object:
        """失败响应的错误码；失败时 data 必须为 null."""
        assert dig(self.json, "data") is None, self.body
        return dig(self.json, "code")


async def call(
    app: FastAPI,
    method: str,
    path: str,
    body: object | None = None,
    headers: Headers = (),
) -> Reply:
    """直接调用 ASGI 应用并收集完整响应."""
    content = b"" if body is None else json.dumps(body).encode()
    sent: list[Message] = []
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": content, "more_body": False}
        _ = await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(_scope(method, path, headers), cast(Receive, receive), cast(Send, send))
    start = sent[0]
    raw_headers = cast(list[tuple[bytes, bytes]], start["headers"])
    return Reply(
        cast(int, start["status"]),
        {key.decode(): value.decode() for key, value in raw_headers},
        b"".join(cast(bytes, item.get("body", b"")) for item in sent[1:]),
    )


@dataclass(frozen=True)
class SseEvent:
    """解析后的一条 SSE 事件."""

    id: str | None
    type: str
    data: object

    @property
    def payload(self) -> object:
        """事件 payload."""
        return dig(self.data, "data", "payload")


async def read_sse(
    app: FastAPI,
    path: str,
    until: Callable[[list[SseEvent]], bool],
    headers: Headers = (),
) -> list[SseEvent]:
    """打开事件流直到条件满足或服务端结束，然后断开."""
    buffer = ""
    events: list[SseEvent] = []
    done = asyncio.Event()
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        _ = await done.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        nonlocal buffer
        if message["type"] != "http.response.body":
            return
        buffer += cast(bytes, message.get("body", b"")).decode()
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            fields = dict(
                line.split(": ", 1) for line in block.splitlines() if ": " in line
            )
            if "event" in fields:
                events.append(
                    SseEvent(
                        fields.get("id"),
                        fields["event"],
                        cast(object, json.loads(fields["data"])),
                    )
                )
        if until(events) or not message.get("more_body", False):
            done.set()

    async with asyncio.timeout(5):
        await app(
            _scope("GET", path, headers), cast(Receive, receive), cast(Send, send)
        )
    return events


async def wait_until(predicate: Callable[[], bool], timeout_s: float = 5) -> None:
    """等待异步流程达到条件."""
    async with asyncio.timeout(timeout_s):
        while not predicate():
            await asyncio.sleep(0.01)


@dataclass
class Harness:
    """一次装配：模拟设备、可控模型、真实服务层与路由."""

    model: FakeMessagesListChatModel
    robot: RobotApplication
    service: RobotApiService
    http: FastAPI

    def script(
        self, responses: list[BaseMessage], sleep_s: float | None = None
    ) -> None:
        """设置模型后续回答；sleep_s 模拟推理耗时."""
        self.model.responses = responses
        self.model.i = 0
        self.model.sleep = sleep_s

    def messages(self, task_id: int) -> list[MessageRecord]:
        """服务端登记的消息."""
        return list(self.service._views[task_id].messages.values())  # pyright: ignore[reportPrivateUsage]

    async def create_task(self, request_id: str = "t1") -> int:
        """创建任务并返回编号."""
        reply = await call(
            self.http, "POST", "/api/v1/tasks", {"client_request_id": request_id}
        )
        assert reply.status == 201, reply.body
        task_id = dig(reply.data, "task_id")
        assert isinstance(task_id, int)
        return task_id

    async def snapshot(self, task_id: int) -> object:
        """读取任务快照."""
        reply = await call(self.http, "GET", f"/api/v1/tasks/{task_id}")
        assert reply.status == 200, reply.body
        return reply.data

    async def say(self, task_id: int, text: str, request_id: str = "m1") -> Reply:
        """发送文字消息."""
        return await call(
            self.http,
            "POST",
            f"/api/v1/tasks/{task_id}/messages",
            {"client_request_id": request_id, "text": text},
        )


@asynccontextmanager
async def harness(
    *, audio: bool = False, preview: CameraPreview | None = None
) -> AsyncGenerator[Harness]:
    """装配并启动；退出时关闭服务与应用."""
    evidence = EvidenceStore()
    model = FakeMessagesListChatModel(responses=[])
    robot = RobotApplication(
        SimulatedAdapter(),
        SimulatedPerception(evidence),
        model,
        _audio=build_simulated_audio(evidence, "热水壶能碰吗") if audio else None,
        _evidence=evidence,
    )
    service = RobotApiService(
        robot,
        _services=ServiceStates("ready", "not_configured", "ready", "ready"),
        _simulated=True,
        _preview=preview,
        _evidence=evidence,
    )
    http = build_http_app(ServiceSlot(service), heartbeat_s=0.05)
    with patch.object(FakeMessagesListChatModel, "bind_tools", return_value=model):
        await robot.start()
        try:
            await service.start()
            yield Harness(model, robot, service, http)
        finally:
            try:
                await service.close()
            finally:
                await robot.close()


class HttpApiTests(unittest.IsolatedAsyncioTestCase):
    """文字任务、错误码、事件流与证据."""

    async def test_system_reports_internal_state_only(self) -> None:
        """系统状态：字段齐全、模拟标记、相机未配置时 error 非空."""
        async with harness() as h:
            reply = await call(h.http, "GET", "/api/v1/system")
            self.assertEqual(reply.status, 200)
            self.assertEqual(reply.headers["content-type"], "application/json")
            raw = reply.data
            assert isinstance(raw, dict)
            data = cast(dict[str, object], raw)
            self.assertEqual(
                set(data),
                {
                    "instance_id",
                    "active_task_id",
                    "simulated",
                    "runtime_blocked_reason",
                    "robot",
                    "camera",
                    "audio",
                    "services",
                },
            )
            self.assertEqual(dig(data, "instance_id"), h.service.instance_id)
            self.assertIsNone(dig(data, "active_task_id"))
            self.assertIs(dig(data, "simulated"), True)
            self.assertIs(dig(data, "camera", "available"), False)
            self.assertTrue(dig(data, "camera", "error"))
            self.assertIs(dig(data, "audio", "available"), False)

    async def test_create_task_deduplicates_and_allows_one_active_task(self) -> None:
        """同编号同内容返回原结果，不同内容 40009，已有活动任务 40010."""
        async with harness() as h:
            body = {"client_request_id": "a", "user_target": "热水安全"}
            first = await call(h.http, "POST", "/api/v1/tasks", body)
            again = await call(h.http, "POST", "/api/v1/tasks", body)
            self.assertEqual(first.status, 201)
            self.assertEqual(first.data, again.data)
            self.assertEqual(dig(first.data, "user_target"), "热水安全")
            self.assertEqual(dig(first.data, "status"), "pending")
            conflict = await call(
                h.http, "POST", "/api/v1/tasks", {"client_request_id": "a"}
            )
            self.assertEqual((conflict.status, conflict.code), (409, 40009))
            busy = await call(
                h.http, "POST", "/api/v1/tasks", {"client_request_id": "b"}
            )
            self.assertEqual((busy.status, busy.code), (409, 40010))
            system = (await call(h.http, "GET", "/api/v1/system")).data
            self.assertEqual(dig(system, "active_task_id"), dig(first.data, "task_id"))

    async def test_validation_and_not_found_use_unified_errors(self) -> None:
        """未定义字段、非法编号、空白、超长与 NaN 返回 40001；不存在返回 40401."""
        cases: list[tuple[str, str, object | None, int, int]] = [
            (
                "POST",
                "/api/v1/tasks",
                {"client_request_id": "x", "extra": 1},
                422,
                40001,
            ),
            ("POST", "/api/v1/tasks", {}, 422, 40001),
            ("POST", "/api/v1/tasks", {"client_request_id": "   "}, 422, 40001),
            ("POST", "/api/v1/tasks", {"client_request_id": 1}, 422, 40001),
            ("GET", "/api/v1/tasks/0", None, 422, 40001),
            ("GET", "/api/v1/tasks/abc", None, 422, 40001),
            ("GET", "/api/v1/tasks/99", None, 404, 40401),
            ("GET", "/api/v1/nothing", None, 404, 40401),
            ("GET", "/api/v1/evidence/" + "a" * 32, None, 404, 40401),
            ("GET", "/api/v1/evidence/not-hex", None, 422, 40001),
            ("GET", "/api/v1/camera/snapshot", None, 503, 50002),
            ("GET", "/api/v1/camera/snapshot?max_width=10", None, 422, 40001),
        ]
        async with harness() as h:
            for method, path, body, http_status, code in cases:
                with self.subTest(path=path, body=body):
                    reply = await call(h.http, method, path, body)
                    self.assertEqual((reply.status, reply.code), (http_status, code))
            task_id = await h.create_task()
            invalid: list[tuple[str, str, object]] = [
                (
                    "POST",
                    f"/api/v1/tasks/{task_id}/recordings",
                    {"client_request_id": "r", "max_duration_s": float("nan")},
                ),
                ("PATCH", f"/api/v1/tasks/{task_id}", {"status": "running"}),
                (
                    "POST",
                    f"/api/v1/tasks/{task_id}/messages",
                    {"client_request_id": "m", "text": "x" * 2001},
                ),
            ]
            for method, path, body in invalid:
                with self.subTest(path=path, body=body):
                    reply = await call(h.http, method, path, body)
                    self.assertEqual((reply.status, reply.code), (422, 40001))

    async def test_message_reply_snapshot_and_busy_rejection(self) -> None:
        """消息受理 202；处理中再发 40010；回答以 assistant 消息出现并关联原消息."""
        async with harness() as h:
            h.script([AIMessage("热水壶很烫，请让大人帮忙。")])
            task_id = await h.create_task()
            accepted = await h.say(task_id, "热水壶能碰吗")
            self.assertEqual(accepted.status, 202)
            self.assertEqual(
                [dig(accepted.data, key) for key in ("role", "source", "status")],
                ["user", "text", "accepted"],
            )
            busy = await h.say(task_id, "再说一遍", "m2")
            self.assertEqual((busy.status, busy.code), (409, 40010))
            await wait_until(
                lambda: any(item.status == "answered" for item in h.messages(task_id))
            )
            snapshot = await h.snapshot(task_id)
            user, reply = items(snapshot, "messages")
            self.assertEqual(dig(user, "status"), "answered")
            self.assertEqual(
                [dig(reply, key) for key in ("role", "status", "source", "text")],
                ["assistant", "final", "agent", "热水壶很烫，请让大人帮忙。"],
            )
            self.assertEqual(dig(reply, "reply_to"), dig(user, "message_id"))
            self.assertIsNone(dig(reply, "speech_action_id"))
            self.assertEqual(
                dig(snapshot, "truncated"), {"messages": False, "actions": False}
            )
            replay = await h.say(task_id, "热水壶能碰吗")
            self.assertEqual(
                dig(replay.data, "message_id"), dig(accepted.data, "message_id")
            )

    async def test_events_replay_from_cursor_and_reset_on_foreign_instance(
        self,
    ) -> None:
        """游标后的事件连续重放；他实例游标得到无 id 的 stream.reset；缺游标 40001."""
        async with harness() as h:
            h.script([AIMessage("好的。")])
            task_id = await h.create_task()
            cursor = dig(await h.snapshot(task_id), "event_cursor")
            assert isinstance(cursor, str)
            start = int(cursor.split(":")[1])
            _ = await h.say(task_id, "你好")
            events = await read_sse(
                h.http,
                f"/api/v1/tasks/{task_id}/events?after={cursor}",
                lambda found: any(
                    event.type == "message.updated"
                    and dig(event.payload, "status") == "answered"
                    for event in found
                ),
            )
            sequences = [dig(event.data, "data", "sequence") for event in events]
            self.assertEqual(sequences, list(range(start + 1, start + 1 + len(events))))
            self.assertEqual(
                [event.id for event in events],
                [f"{h.service.instance_id}:{value}" for value in sequences],
            )
            user_statuses = [
                dig(event.payload, "status")
                for event in events
                if event.type == "message.updated"
                and dig(event.payload, "role") == "user"
            ]
            self.assertEqual(user_statuses, ["accepted", "processing", "answered"])

            foreign = await read_sse(
                h.http,
                f"/api/v1/tasks/{task_id}/events?after=bad",
                bool,
                headers=(("Last-Event-ID", "f" * 32 + ":1"),),
            )
            self.assertEqual(
                [(event.type, event.id) for event in foreign], [("stream.reset", None)]
            )
            self.assertEqual(dig(foreign[0].payload, "reason"), "instance_changed")
            missing = await call(h.http, "GET", f"/api/v1/tasks/{task_id}/events")
            self.assertEqual((missing.status, missing.code), (422, 40001))
            malformed = await call(
                h.http, "GET", f"/api/v1/tasks/{task_id}/events?after=bad"
            )
            self.assertEqual(malformed.code, 40001)

    async def test_cancel_returns_202_then_terminal_and_repeats_200(self) -> None:
        """停止先返回 202，终态经事件发布；再次停止返回 200；已结束不能确认完成."""
        async with harness() as h:
            task_id = await h.create_task()
            path = f"/api/v1/tasks/{task_id}"
            first = await call(h.http, "PATCH", path, {"status": "cancelling"})
            self.assertEqual(first.status, 202)
            self.assertIn(dig(first.data, "status"), {"cancelling", "cancelled"})
            await wait_until(
                lambda: h.robot.gateway.get_task(task_id).status == TaskStatus.CANCELLED
            )
            again = await call(h.http, "PATCH", path, {"status": "cancelling"})
            self.assertEqual(
                (again.status, dig(again.data, "status")), (200, "cancelled")
            )
            finish = await call(h.http, "PATCH", path, {"status": "completed"})
            self.assertEqual(finish.code, 40010)
            self.assertEqual(
                dig(await h.snapshot(task_id), "task", "status"), "cancelled"
            )
            _ = await h.create_task("t2")

    async def test_cancel_during_processing_marks_message_cancelled(self) -> None:
        """模型推理中停止任务：消息记为 cancelled，任务终态，不生成迟到回答."""
        async with harness() as h:
            h.script([AIMessage("迟到的回答")], sleep_s=0.3)
            task_id = await h.create_task()
            _ = await h.say(task_id, "讲个故事")
            await wait_until(
                lambda: any(item.status == "processing" for item in h.messages(task_id))
            )
            reply = await call(
                h.http, "PATCH", f"/api/v1/tasks/{task_id}", {"status": "cancelling"}
            )
            self.assertEqual(reply.status, 202)
            await wait_until(
                lambda: (
                    [item.status for item in h.messages(task_id)] == ["cancelled"]
                    and h.robot.gateway.get_task(task_id).status == TaskStatus.CANCELLED
                )
            )
            await asyncio.sleep(0.4)
            self.assertEqual(len(h.messages(task_id)), 1)

    async def test_model_unreachable_rejects_message_with_50002(self) -> None:
        """模型服务连不上：消息 rejected 且错误码 50002，宿主停止任务."""
        async with harness() as h:
            task_id = await h.create_task()
            with patch.object(
                FakeMessagesListChatModel,
                "ainvoke",
                side_effect=httpx.ConnectError("refused"),
            ):
                _ = await h.say(task_id, "你好")
                await wait_until(
                    lambda: (
                        [item.status for item in h.messages(task_id)] == ["rejected"]
                    )
                )
            message = items(await h.snapshot(task_id), "messages")[0]
            self.assertEqual(dig(message, "error", "code"), 50002)
            await wait_until(
                lambda: h.robot.gateway.get_task(task_id).status == TaskStatus.CANCELLED
            )

    async def test_completed_requires_user_confirmation(self) -> None:
        """确认完成返回 200 且写入结束时间；完成后可以创建新任务."""
        async with harness() as h:
            task_id = await h.create_task()
            path = f"/api/v1/tasks/{task_id}"
            reply = await call(h.http, "PATCH", path, {"status": "completed"})
            self.assertEqual(
                (reply.status, dig(reply.data, "status")), (200, "completed")
            )
            self.assertIsNotNone(dig(reply.data, "ended_at"))
            again = await call(h.http, "PATCH", path, {"status": "completed"})
            self.assertEqual(again.status, 200)
            _ = await h.create_task("t2")

    async def test_observation_event_and_evidence_content(self) -> None:
        """机器人观察进入快照，画面可按编号读取，任务结束释放后返回 40401 与 null."""
        async with harness() as h:
            h.script(
                [
                    AIMessage(
                        "",
                        tool_calls=[
                            {
                                "name": "observe_scene",
                                "args": {"question": "桌上有什么？"},
                                "id": "o1",
                            }
                        ],
                    ),
                    AIMessage("这是模拟画面。"),
                ]
            )
            task_id = await h.create_task()
            _ = await h.say(task_id, "看看桌上有什么")
            await wait_until(
                lambda: any(item.status == "answered" for item in h.messages(task_id))
            )
            observation = dig(await h.snapshot(task_id), "latest_observation")
            self.assertEqual(dig(observation, "question"), "桌上有什么？")
            self.assertIs(dig(observation, "simulated"), True)
            self.assertEqual(dig(observation, "objects"), [])
            evidence_id = dig(observation, "frame_evidence_id")
            assert isinstance(evidence_id, str)
            content = await call(h.http, "GET", f"/api/v1/evidence/{evidence_id}")
            self.assertEqual(
                (content.status, content.headers["content-type"]), (200, "image/png")
            )
            self.assertTrue(content.body.startswith(b"\x89PNG"))
            _ = await call(
                h.http, "PATCH", f"/api/v1/tasks/{task_id}", {"status": "completed"}
            )
            released = await call(h.http, "GET", f"/api/v1/evidence/{evidence_id}")
            self.assertEqual(released.code, 40401)
            self.assertIsNone(
                dig(
                    await h.snapshot(task_id), "latest_observation", "frame_evidence_id"
                )
            )

    async def test_recording_unavailable_without_speech(self) -> None:
        """未配置语音时开始录音返回 50002."""
        async with harness() as h:
            task_id = await h.create_task()
            reply = await call(
                h.http,
                "POST",
                f"/api/v1/tasks/{task_id}/recordings",
                {"client_request_id": "r1"},
            )
            self.assertEqual((reply.status, reply.code), (503, 50002))


class SpeechApiTests(unittest.IsolatedAsyncioTestCase):
    """模拟语音：录音生成用户消息，回答通过 speak 动作播报."""

    async def test_recording_transcribes_into_message_and_speaks_reply(self) -> None:
        """结束录音 202；识别文本自动成为 speech 消息；回答关联已成功的 speak 动作."""
        async with harness(audio=True) as h:
            h.script([AIMessage("热水壶不能碰。")])
            task_id = await h.create_task()
            path = f"/api/v1/tasks/{task_id}/recordings"
            started = await call(
                h.http, "POST", path, {"client_request_id": "r1", "max_duration_s": 10}
            )
            self.assertEqual(
                (started.status, dig(started.data, "status")), (201, "recording")
            )
            recording_id = dig(started.data, "recording_id")
            assert isinstance(recording_id, str)
            second = await call(h.http, "POST", path, {"client_request_id": "r2"})
            self.assertEqual(second.code, 40010)
            self.assertEqual((await h.say(task_id, "你好", "m")).code, 40010)
            self.assertEqual(
                dig(await h.snapshot(task_id), "active_recording", "recording_id"),
                recording_id,
            )
            finish = await call(
                h.http, "PATCH", f"{path}/{recording_id}", {"status": "transcribing"}
            )
            self.assertEqual(
                (finish.status, dig(finish.data, "status")), (202, "transcribing")
            )
            await wait_until(
                lambda: (
                    any(item.status == "answered" for item in h.messages(task_id))
                    and all(
                        record.status.is_terminal
                        for record in h.robot.runtime.list_actions(task_id)
                    )
                )
            )
            snapshot = await h.snapshot(task_id)
            self.assertIsNone(dig(snapshot, "active_recording"))
            user, reply = items(snapshot, "messages")
            self.assertEqual(
                (dig(user, "source"), dig(user, "text")), ("speech", "热水壶能碰吗")
            )
            view = h.service._views[task_id]  # pyright: ignore[reportPrivateUsage]
            record = view.recordings[recording_id]
            self.assertEqual(
                (record.status, record.transcript, record.message_id),
                ("completed", "热水壶能碰吗", dig(user, "message_id")),
            )
            speech_id = dig(reply, "speech_action_id")
            speak = [
                action
                for action in items(snapshot, "actions")
                if dig(action, "action_id") == speech_id
            ]
            self.assertEqual(
                [(dig(item, "skill_name"), dig(item, "status")) for item in speak],
                [("speak", "succeeded")],
            )
            again = await call(
                h.http, "PATCH", f"{path}/{recording_id}", {"status": "transcribing"}
            )
            self.assertEqual(again.code, 40010)

    async def test_cancel_recording_discards_text(self) -> None:
        """放弃录音返回 200 与 cancelled，不生成消息，可以重新录音."""
        async with harness(audio=True) as h:
            task_id = await h.create_task()
            path = f"/api/v1/tasks/{task_id}/recordings"
            started = await call(h.http, "POST", path, {"client_request_id": "r1"})
            recording_id = dig(started.data, "recording_id")
            assert isinstance(recording_id, str)
            for _ in range(2):
                reply = await call(
                    h.http, "PATCH", f"{path}/{recording_id}", {"status": "cancelled"}
                )
                self.assertEqual(
                    (reply.status, dig(reply.data, "status")), (200, "cancelled")
                )
            await asyncio.sleep(0.05)
            self.assertEqual(items(await h.snapshot(task_id), "messages"), [])
            again = await call(h.http, "POST", path, {"client_request_id": "r2"})
            self.assertEqual(again.status, 201)
            unknown = await call(
                h.http, "PATCH", f"{path}/{'b' * 32}", {"status": "cancelled"}
            )
            self.assertEqual(unknown.code, 40401)


class CameraSnapshotTests(unittest.IsolatedAsyncioTestCase):
    """预览采集最近一帧并按需缩小."""

    async def test_snapshot_returns_jpeg_and_resizes(self) -> None:
        """首次请求启动采集；返回不缓存的 JPEG，预览帧不留存在证据存储中."""
        evidence = EvidenceStore()
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "frame.png"
            Image.new("RGB", (640, 480), (200, 100, 50)).save(image_path)
            camera = FileCamera(evidence, "main-camera", str(image_path))
            await camera.connect()
            preview = CameraPreview(
                camera, evidence, _interval_s=0.01, _warmup_frames=1
            )
            async with harness(preview=preview) as h:
                full = await call(h.http, "GET", "/api/v1/camera/snapshot")
                self.assertEqual(full.headers["content-type"], "image/jpeg")
                self.assertEqual(full.headers["cache-control"], "no-store")
                with Image.open(io.BytesIO(full.body)) as image:
                    self.assertEqual((image.format, image.width), ("JPEG", 640))
                small = await call(
                    h.http, "GET", "/api/v1/camera/snapshot?max_width=320"
                )
                with Image.open(io.BytesIO(small.body)) as image:
                    self.assertEqual(image.size, (320, 240))
                system = (await call(h.http, "GET", "/api/v1/system")).data
                self.assertIs(dig(system, "camera", "available"), True)
                self.assertIsNotNone(dig(system, "camera", "last_frame_at"))
            await camera.close()
            self.assertEqual(evidence.used_bytes, 0)


class EventLogTests(unittest.IsolatedAsyncioTestCase):
    """事件保留、游标失效与慢订阅者."""

    async def test_expired_cursor_and_slow_subscriber_reset(self) -> None:
        """超出保留范围、超前的游标与积压超限的订阅都得到重置原因."""
        log = TaskEventLog("a" * 32, 1, _retention=3, _subscriber_capacity=2)
        for index in range(5):
            log.publish("task.updated", {"n": index})
        with self.assertRaises(StreamResetError) as expired:
            _ = await log.subscribe("a" * 32, 0).next(0.01)
        self.assertEqual(expired.exception.reason, "cursor_expired")
        live = log.subscribe("a" * 32, 2)
        replayed = [await live.next(0.01) for _ in range(3)]
        self.assertEqual([item.sequence for item in replayed if item], [3, 4, 5])
        self.assertIsNone(await live.next(0.01))
        for index in range(6):
            log.publish("task.updated", {"n": index})
        self.assertTrue(live.closed)
        with self.assertRaises(StreamResetError) as invalid:
            _ = await log.subscribe("a" * 32, log.sequence + 1).next(0.01)
        self.assertEqual(invalid.exception.reason, "cursor_invalid")


if __name__ == "__main__":
    _ = unittest.main()
