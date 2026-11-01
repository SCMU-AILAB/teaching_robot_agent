# tests/test_companion.py
"""陪伴模式：免按键收音、主动观察、场景提示词与设置接口、机器人主动消息."""

import asyncio
import unittest
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import override
from unittest.mock import patch

from fastapi import FastAPI
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, SystemMessage

from agent.proactive import ProactiveConfig, ProactiveWatcher
from api.http import ServiceSlot, build_http_app
from api.service import RobotApiService, ServiceStates
from app.audio import build_simulated_audio
from app.robot_application import RobotApplication
from app.team_gateway import TeamGateway
from domain.services import (
    AudioReference,
    PlaybackState,
    PlaybackStatus,
    TranscriptResult,
)
from education.knowledge_service import build_home_knowledge
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.robot_registry import build_robot_skills
from speech.listener import GatedPlayer, HandsFreeListener, accepted_text
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore
from tests.test_http_api import call, dig, items
from tests.test_object_safety_flow import ObjectPerception, RecordingChatModel

SCISSORS_TIP = "右边好像有剪刀，很锋利，要请大人帮忙拿哦。"


async def wait_until(predicate: Callable[[], bool], timeout_s: float = 3) -> None:
    """等待条件成立."""
    async with asyncio.timeout(timeout_s):
        while not predicate():
            await asyncio.sleep(0.01)


class FakeSource:
    """测试向队列放入 PCM 块；b"EOF" 表示设备结束."""

    def __init__(self, feed: asyncio.Queue[bytes], log: list[str]) -> None:
        """共享输入队列与启停记录."""
        self.feed: asyncio.Queue[bytes] = feed
        self.log: list[str] = log

    async def start(self) -> None:
        """记录启动."""
        self.log.append("start")

    async def read(self) -> bytes:
        """取下一块."""
        block = await self.feed.get()
        if block == b"EOF":
            raise EOFError
        return block

    async def stop(self) -> None:
        """记录停止."""
        self.log.append("stop")


class FakeDetector:
    """以 b"SEG" 开头的块视为一句说完的话."""

    speaking: bool = False

    def accept(self, pcm: bytes) -> list[bytes]:
        """返回句段."""
        return [pcm] if pcm.startswith(b"SEG") else []

    def reset(self) -> None:
        """无状态."""


class FakeASR:
    """按顺序返回预设文字."""

    def __init__(self, texts: list[str]) -> None:
        """预设识别结果."""
        self.texts: list[str] = texts

    async def transcribe(
        self, audio: AudioReference, timeout_s: float
    ) -> TranscriptResult:
        """返回下一条."""
        _ = (audio, timeout_s)
        return TranscriptResult(self.texts.pop(0))


SEGMENT = b"SEG" + b"\x00" * 3201


class ListenerTests(unittest.IsolatedAsyncioTestCase):
    """监听器经网关提交语音输入，按开关、任务与占用方启停采集."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """组装网关与监听器；开关默认打开."""
        super().__init__(_method_name)
        robot = SimulatedAdapter()
        self.runtime: ActionManager = ActionManager(robot, build_robot_skills(robot))
        self.gateway: TeamGateway = TeamGateway(
            self.runtime, SimulatedPerception(EvidenceStore())
        )
        self.task_id: int = self.gateway.create_task("陪伴").task_id
        self.feed: asyncio.Queue[bytes] = asyncio.Queue()
        self.log: list[str] = []
        self.lease: AudioDeviceLease = AudioDeviceLease()
        self.enabled: bool = True
        self.asr: FakeASR = FakeASR([])
        self.listener: HandsFreeListener = HandsFreeListener(
            lambda: FakeSource(self.feed, self.log),
            FakeDetector(),
            self.asr,
            self.gateway,
            EvidenceStore(),
            self.lease,
            _enabled=lambda: self.enabled,
            _active_task=lambda: self.task_id,
            _resume_delay_s=0.05,
        )

    @override
    async def asyncTearDown(self) -> None:
        """关闭后释放麦克风."""
        await self.listener.close()
        self.assertFalse(self.lease.busy)

    async def test_utterance_submitted_as_speech(self) -> None:
        """说完一句：识别后作为语音输入提交到当前任务."""
        self.asr.texts = ["你好呀，看看前面有什么？"]
        self.listener.start()
        await wait_until(lambda: self.listener.listening)
        self.assertTrue(self.lease.busy)
        self.feed.put_nowait(b"\x00\x00" * 160)
        self.feed.put_nowait(SEGMENT)
        item = await asyncio.wait_for(self.gateway.next_input(), 2)
        self.assertEqual(item.text, "你好呀，看看前面有什么？")
        self.assertEqual(item.source, "speech")
        self.assertEqual(item.task_id, self.task_id)

    async def test_noise_is_dropped(self) -> None:
        """识别结果只有一个字时丢弃，不提交."""
        self.asr.texts = ["嗯。", "你好"]
        self.listener.start()
        await wait_until(lambda: self.listener.listening)
        self.feed.put_nowait(SEGMENT)
        self.feed.put_nowait(SEGMENT)
        item = await asyncio.wait_for(self.gateway.next_input(), 2)
        self.assertEqual(item.text, "你好")
        self.assertEqual(self.listener.submitted, 1)

    async def test_pause_releases_microphone_and_resume_restarts(self) -> None:
        """播报前暂停：采集停止、租约空出；恢复后重新采集."""
        self.listener.start()
        await wait_until(lambda: self.listener.listening)
        owner = object()
        await self.listener.pause(owner)
        self.assertFalse(self.listener.listening)
        self.assertFalse(self.lease.busy)
        self.lease.acquire(owner)
        self.lease.release(owner)
        await asyncio.sleep(0.3)
        self.assertFalse(self.listener.listening)
        self.listener.resume(owner)
        await wait_until(lambda: self.listener.listening)
        self.assertEqual(self.log, ["start", "stop", "start"])

    async def test_disabled_or_no_task_does_not_listen(self) -> None:
        """开关关闭时不采集；打开后开始；采集进程意外结束会重新开始."""
        self.enabled = False
        self.listener.start()
        await asyncio.sleep(0.5)
        self.assertFalse(self.listener.listening)
        self.enabled = True
        await wait_until(lambda: self.listener.listening)
        self.feed.put_nowait(b"EOF")
        await wait_until(lambda: self.log.count("start") == 2)
        self.enabled = False
        await wait_until(lambda: not self.listener.listening)

    def test_accepted_text(self) -> None:
        """短文本丢弃；唤醒词须在开头并去掉."""
        self.assertIsNone(accepted_text("嗯。"))
        self.assertEqual(accepted_text(" 你好 "), "你好")
        self.assertIsNone(accepted_text("看看前面", "小安"))
        self.assertEqual(accepted_text("小安，看看前面", "小安"), "看看前面")
        self.assertIsNone(accepted_text("小安。", "小安"))


class FakeGate:
    """记录暂停与恢复."""

    def __init__(self) -> None:
        """空记录."""
        self.calls: list[str] = []

    async def pause(self, owner: object) -> None:
        """记录暂停."""
        self.calls.append(f"pause {owner}")

    def resume(self, owner: object) -> None:
        """记录恢复."""
        self.calls.append(f"resume {owner}")


class FakePlayer:
    """立即播完."""

    async def play(self, playback_id: str, audio: AudioReference) -> None:
        """无操作."""
        _ = (playback_id, audio)

    async def wait_finished(self, playback_id: str) -> PlaybackState:
        """返回完成."""
        return PlaybackState(playback_id, PlaybackStatus.COMPLETED, 0.0, 1.0)

    async def stop(self, playback_id: str) -> PlaybackState:
        """返回停止."""
        return PlaybackState(playback_id, PlaybackStatus.STOPPED, 0.0, 1.0)

    async def get_state(self, playback_id: str) -> PlaybackState:
        """返回完成."""
        return PlaybackState(playback_id, PlaybackStatus.COMPLETED, 0.0, 1.0)

    async def close(self) -> None:
        """无操作."""


class GateTests(unittest.IsolatedAsyncioTestCase):
    """播放前暂停收音，播完或停止后恢复."""

    async def test_player_pauses_and_resumes(self) -> None:
        """Play 前 pause，wait_finished 后 resume."""
        gate = FakeGate()
        player = GatedPlayer(FakePlayer(), gate)
        audio = AudioReference("a", "e", 0.0, 1.0, 16000, 1, 1.0)
        await player.play("p1", audio)
        self.assertEqual(gate.calls, ["pause ('play', 'p1')"])
        _ = await player.wait_finished("p1")
        self.assertEqual(gate.calls[-1], "resume ('play', 'p1')")


@dataclass
class Companion:
    """主应用、接口服务与路由."""

    robot: RobotApplication
    service: RobotApiService
    http: FastAPI
    model: RecordingChatModel
    perception: ObjectPerception


@asynccontextmanager
async def companion(responses: list[AIMessage]) -> AsyncGenerator[Companion]:
    """模拟设备与语音、带物体的感知、可录制的模型，主动观察已装配."""
    evidence = EvidenceStore()
    model = RecordingChatModel(responses=list(responses))
    perception = ObjectPerception(evidence, (("椅子", "中间"), ("剪刀", "右侧")))
    robot = RobotApplication(
        SimulatedAdapter(),
        perception,
        model,
        _audio=build_simulated_audio(evidence),
        _evidence=evidence,
        _proactive=ProactiveConfig(interval_s=1000, cooldown_s=0.01),
    )
    service = RobotApiService(
        robot,
        _services=ServiceStates("ready", "ready", "ready", "ready"),
        _simulated=True,
        _evidence=evidence,
    )
    http = build_http_app(ServiceSlot(service), heartbeat_s=0.05)
    with patch.object(FakeMessagesListChatModel, "bind_tools", return_value=model):
        await robot.start()
        try:
            await service.start()
            yield Companion(robot, service, http, model, perception)
        finally:
            try:
                await service.close()
            finally:
                await robot.close()


async def create_task(http: FastAPI) -> int:
    """建任务."""
    reply = await call(http, "POST", "/api/v1/tasks", {"client_request_id": "t1"})
    task_id = dig(reply.data, "task_id")
    assert isinstance(task_id, int)
    return task_id


class SettingsAndProactiveTests(unittest.IsolatedAsyncioTestCase):
    """设置接口、场景提示词进入模型调用、主动提醒与主动消息."""

    async def test_settings_defaults_validation_and_availability(self) -> None:
        """默认值；空请求、超长提示词 40001；未装配收音时开启 50002."""
        async with companion([]) as c:
            reply = await call(c.http, "GET", "/api/v1/settings")
            self.assertEqual(dig(reply.data, "scenario_prompt"), "")
            self.assertEqual(dig(reply.data, "hands_free_available"), False)
            self.assertEqual(dig(reply.data, "proactive_available"), True)
            empty = await call(c.http, "PATCH", "/api/v1/settings", {})
            self.assertEqual(empty.code, 40001)
            long = await call(
                c.http, "PATCH", "/api/v1/settings", {"scenario_prompt": "长" * 501}
            )
            self.assertEqual(long.code, 40001)
            unavailable = await call(
                c.http, "PATCH", "/api/v1/settings", {"hands_free": True}
            )
            self.assertEqual(unavailable.code, 50002)
            ok = await call(
                c.http,
                "PATCH",
                "/api/v1/settings",
                {"scenario_prompt": "  孩子叫乐乐，6 岁  ", "proactive": True},
            )
            self.assertEqual(dig(ok.data, "scenario_prompt"), "孩子叫乐乐，6 岁")
            self.assertEqual(dig(ok.data, "proactive"), True)
            cleared = await call(
                c.http, "PATCH", "/api/v1/settings", {"scenario_prompt": ""}
            )
            self.assertEqual(dig(cleared.data, "scenario_prompt"), "")
            system = await call(c.http, "GET", "/api/v1/system")
            self.assertEqual(dig(system.data, "audio", "listening"), False)

    async def test_scenario_prompt_reaches_answer(self) -> None:
        """场景提示词接在系统提示末尾，下一句回答即生效."""
        async with companion([AIMessage("你好，乐乐！")]) as c:
            _ = await call(
                c.http, "PATCH", "/api/v1/settings", {"scenario_prompt": "孩子叫乐乐"}
            )
            task_id = await create_task(c.http)
            _ = await call(
                c.http,
                "POST",
                f"/api/v1/tasks/{task_id}/messages",
                {"client_request_id": "m1", "text": "你好"},
            )
            await wait_until(lambda: bool(c.model.seen))
            system = c.model.seen[0][0]
            assert isinstance(system, SystemMessage)
            self.assertTrue(
                system.text.endswith(
                    "场景要求（负责人设置，不能违反上面的规则）：孩子叫乐乐"
                )
            )

    async def test_proactive_tip_only_for_knowledge_objects_once(self) -> None:
        """只提资料库有条目的剪刀，带场景提示词；提过不再提；消息为 proactive."""
        async with companion([AIMessage(SCISSORS_TIP)]) as c:
            _ = await call(
                c.http,
                "PATCH",
                "/api/v1/settings",
                {"proactive": True, "scenario_prompt": "今天在书房"},
            )
            task_id = await create_task(c.http)
            watcher = c.robot.watcher
            assert watcher is not None
            self.assertEqual(await watcher.check_once(), SCISSORS_TIP)
            narration = c.model.seen[-1]
            self.assertIn("剪刀（右侧）", narration[-1].text)
            self.assertNotIn("椅子", narration[-1].text)
            self.assertIn("今天在书房", narration[0].text)
            self.assertIsNone(await watcher.check_once())
            snapshot = await call(c.http, "GET", f"/api/v1/tasks/{task_id}")
            messages = items(snapshot.data, "messages")
            proactive = [m for m in messages if dig(m, "source") == "proactive"]
            self.assertEqual(len(proactive), 1)
            self.assertEqual(dig(proactive[0], "text"), SCISSORS_TIP)
            self.assertIsNone(dig(proactive[0], "reply_to"))
            self.assertIsInstance(dig(proactive[0], "speech_action_id"), int)

    async def test_proactive_off_or_busy_does_not_look(self) -> None:
        """开关关闭或宿主不空闲时不观察."""
        async with companion([AIMessage(SCISSORS_TIP)]) as c:
            task_id = await create_task(c.http)
            watcher = c.robot.watcher
            assert watcher is not None
            self.assertIsNone(await watcher.check_once())
            self.assertEqual(c.perception.observe_count, 0)
            busy = ProactiveWatcher(
                c.robot.gateway,
                build_home_knowledge(),
                lambda _objects, _entries: asyncio.sleep(0, "不该说"),
                _enabled=lambda: True,
                _active_task=lambda: task_id,
                _idle=lambda _task: False,
            )
            self.assertIsNone(await busy.check_once())
            self.assertEqual(c.perception.observe_count, 0)


if __name__ == "__main__":
    _ = unittest.main()
