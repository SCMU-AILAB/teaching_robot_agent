# tests/test_robot_audio.py
"""验证主应用语音、Agent、Runtime 动作与统一任务停止的完整闭环."""

import asyncio
import unittest
from typing import override
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.audio import AudioAdapters
from app.robot_application import RobotApplication
from domain.models import ActionStatus, TaskStatus
from domain.services import PlaybackState, TranscriptResult
from perception.simulated import SimulatedPerception
from providers.asr.mock import MockASRProvider
from providers.audio.mock import MockAudioRecorder
from providers.audio.player import MockAudioPlayer
from providers.tts.mock import MockTTSProvider
from robot.simulated import SimulatedAdapter
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


class GatedRecorder(MockAudioRecorder):
    """固定录音停止确认时机，验证任务不能提前发布取消终态."""

    def __init__(self, _store: EvidenceStore, _device: AudioDeviceLease) -> None:
        """初始化停止屏障."""
        super().__init__(_store, _device)
        self.stopping: asyncio.Event = asyncio.Event()
        self.allow_stop: asyncio.Event = asyncio.Event()

    @override
    async def cancel(self, recording_id: str) -> None:
        """等待外部确认再释放录音器."""
        self.stopping.set()
        _ = await self.allow_stop.wait()
        await super().cancel(recording_id)


class GatedStartRecorder(MockAudioRecorder):
    """模拟启动尚未登记会话时发生任务停止."""

    def __init__(self, _store: EvidenceStore, _device: AudioDeviceLease) -> None:
        """初始化启动屏障."""
        super().__init__(_store, _device)
        self.starting: asyncio.Event = asyncio.Event()
        self.allow_start: asyncio.Event = asyncio.Event()

    @override
    async def start(self, recording_id: str, max_duration_s: float) -> None:
        """实际取得租约后等待，复现部分初始化."""
        await super().start(recording_id, max_duration_s)
        self.starting.set()
        _ = await self.allow_start.wait()


class UnconfirmedPlayer(MockAudioPlayer):
    """播放已开始后停止确认失败，随后允许关闭测试资源."""

    def __init__(self, _store: EvidenceStore, _device: AudioDeviceLease) -> None:
        """初始化可恢复故障开关."""
        super().__init__(_store, _device)
        self.fail_stop: bool = True

    @override
    async def stop(self, playback_id: str) -> PlaybackState:
        """拒绝假装设备已经停止."""
        if self.fail_stop:
            raise RuntimeError("Speaker stop unconfirmed")
        return await super().stop(playback_id)


class RobotAudioTests(unittest.IsolatedAsyncioTestCase):
    """不联网、不发声；验证业务记录、停止语义和共享设备租约."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """初始化共享模拟依赖，不创建后台作业."""
        super().__init__(_method_name)
        self.evidence: EvidenceStore = EvidenceStore()
        self.device: AudioDeviceLease = AudioDeviceLease()
        self.recorder: MockAudioRecorder = MockAudioRecorder(self.evidence, self.device)
        self.asr: MockASRProvider = MockASRProvider(
            _result=TranscriptResult("热水壶能碰吗？")
        )
        self.tts: MockTTSProvider = MockTTSProvider(self.evidence, _duration_s=0.01)
        self.player: MockAudioPlayer = MockAudioPlayer(self.evidence, self.device)
        self.model: FakeMessagesListChatModel = FakeMessagesListChatModel(
            responses=[AIMessage("请让成年人帮忙取热水。")]
        )

    async def app(self, *, close_error: str | None = None) -> RobotApplication:
        """装配并登记清理；所有正常播报只能经 Runtime."""
        app = RobotApplication(
            SimulatedAdapter(),
            SimulatedPerception(self.evidence),
            self.model,
            _audio=AudioAdapters(
                self.recorder, self.asr, self.tts, self.player, simulated=True
            ),
        )
        await app.start()

        async def close() -> None:
            """错误路径要求关闭仍报告停止失败，而不是静默忽略."""
            if close_error is None:
                await app.close()
            else:
                with self.assertRaisesRegex(RuntimeError, close_error):
                    await app.close()
                self.assertFalse(self.device.busy)

        self.addAsyncCleanup(close)
        return app

    async def test_transcript_agent_runtime_playback_and_dedup(self) -> None:
        """语音转写只消费一次，回答形成已验证播报动作，重放不重复播音."""
        app = await self.app()
        task = await app.create_task("家庭安全")
        await app.start_recording(task.task_id, "input")
        item = await app.finish_recording("input")
        assert item is not None
        with patch.object(
            FakeMessagesListChatModel, "bind_tools", return_value=self.model
        ):
            reply = await app.process_next()
            self.assertEqual(await app.host.handle(item), reply)
        self.assertEqual(self.asr.call_count, 1)
        self.assertEqual(self.player.play_count, 1)
        self.assertEqual(len(reply.action_ids), 1)
        action = app.runtime.get_action(reply.action_ids[0])
        self.assertEqual(action.raw_request.skill_name, "speak")
        self.assertEqual(action.status, ActionStatus.SUCCEEDED)
        assert action.result is not None
        self.assertEqual(action.result.evidence["playback_status"], "completed")
        self.assertIs(action.result.evidence["simulated"], True)
        self.assertEqual(
            (await app.finish_task(task.task_id)).status, TaskStatus.COMPLETED
        )
        self.assertFalse(self.device.busy)

    async def test_task_cancel_waits_for_recording_confirmation(self) -> None:
        """停止方取消等待也不打断资源释放，任务保持 cancelling 到实际停止."""
        recorder = GatedRecorder(self.evidence, self.device)
        self.recorder = recorder
        app = await self.app()
        task = await app.create_task("录音")
        await app.start_recording(task.task_id, "input")
        with self.assertRaises(ValueError):
            _ = await app.finish_task(task.task_id)
        caller = asyncio.create_task(app.cancel_task(task.task_id))
        try:
            async with asyncio.timeout(2):
                _ = await recorder.stopping.wait()
                self.assertEqual(
                    (await app.gateway.get_snapshot(task.task_id)).task.status,
                    TaskStatus.CANCELLING,
                )
                _ = caller.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    _ = await caller
                recorder.allow_stop.set()
                state = await app.cancel_task(task.task_id)
            self.assertEqual(state.status, TaskStatus.CANCELLED)
            self.assertFalse(self.device.busy)
            with self.assertRaises(ValueError):
                await app.start_recording(task.task_id, "late")
        finally:
            recorder.allow_stop.set()
            _ = await asyncio.gather(caller, return_exceptions=True)

    async def test_cancel_during_recording_start(self) -> None:
        """任务取消等待部分启动的录音释放，不遗漏尚未登记的资源."""
        recorder = GatedStartRecorder(self.evidence, self.device)
        self.recorder = recorder
        app = await self.app()
        task = await app.create_task("录音")
        starting = asyncio.create_task(app.start_recording(task.task_id, "input"))
        stopping: asyncio.Task[object] | None = None
        try:
            async with asyncio.timeout(2):
                _ = await recorder.starting.wait()
                with self.assertRaises(ValueError):
                    _ = await app.finish_task(task.task_id)
                stopping = asyncio.create_task(app.cancel_task(task.task_id))
                await asyncio.sleep(0)
                self.assertFalse(stopping.done())
                recorder.allow_start.set()
                with self.assertRaises(ValueError):
                    await starting
                _ = await stopping
            self.assertFalse(self.device.busy)
            self.assertEqual(
                (await app.gateway.get_snapshot(task.task_id)).task.status,
                TaskStatus.CANCELLED,
            )
        finally:
            recorder.allow_start.set()
            _ = await asyncio.gather(starting, return_exceptions=True)
            if stopping is not None:
                _ = await asyncio.gather(stopping, return_exceptions=True)

    async def test_cancel_during_asr_wait(self) -> None:
        """统一取消终止识别，无转写进入后续 Agent."""
        self.asr = MockASRProvider(_release=asyncio.Event())
        app = await self.app()
        task = await app.create_task("识别")
        await app.start_recording(task.task_id, "input", max_duration_s=0.001)
        async with asyncio.timeout(2):
            _ = await self.asr.entered.wait()
            state = await app.cancel_task(task.task_id)
        self.assertEqual(state.status, TaskStatus.CANCELLED)
        assert app.speech_input is not None
        self.assertEqual(app.speech_input.get_state("input"), "cancelled")
        self.assertEqual(self.player.play_count, 0)

    async def test_cancel_synthesis_and_playback(self) -> None:
        """任务取消在合成和播放阶段都终止动作，不假报完成."""
        for synthesize in (True, False):
            with self.subTest(synthesize=synthesize):
                self.tts = MockTTSProvider(
                    self.evidence,
                    _duration_s=10,
                    _release=asyncio.Event() if synthesize else None,
                )
                self.player = MockAudioPlayer(self.evidence, self.device)
                app = await self.app()
                task = await app.create_task("播报")
                _ = app.gateway.submit_text(task.task_id, "你好")
                with patch.object(
                    FakeMessagesListChatModel, "bind_tools", return_value=self.model
                ):
                    worker = asyncio.create_task(app.process_next())
                    async with asyncio.timeout(2):
                        _ = await (
                            self.tts.entered if synthesize else self.player.started
                        ).wait()
                        state = await app.cancel_task(task.task_id)
                        with self.assertRaises(asyncio.CancelledError):
                            _ = await worker
                self.assertEqual(state.status, TaskStatus.CANCELLED)
                self.assertEqual(
                    app.runtime.list_actions()[0].status, ActionStatus.CANCELLED
                )
                self.assertFalse(self.device.busy)
                if synthesize:
                    self.assertEqual(self.player.play_count, 0)
                await app.close()

    async def test_synthesis_failure_preserves_text_and_failed_action(self) -> None:
        """合成失败保留回答文字，动作及任务明确失败."""
        self.tts = MockTTSProvider(
            self.evidence, _error=RuntimeError("TTS unavailable")
        )
        app = await self.app()
        task = await app.create_task("问答")
        _ = app.gateway.submit_text(task.task_id, "你好")
        with patch.object(
            FakeMessagesListChatModel, "bind_tools", return_value=self.model
        ):
            reply = await app.process_next()
        self.assertIn("成年人", reply.text)
        self.assertIn("播报未完成", reply.text)
        self.assertEqual(reply.task_status, TaskStatus.FAILED)
        self.assertEqual(app.runtime.list_actions()[0].status, ActionStatus.FAILED)
        self.assertFalse(self.device.busy)

    async def test_runtime_timeout_and_invalid_args(self) -> None:
        """执行期限覆盖合成，非法参数在生成动作之前拒绝."""
        self.tts = MockTTSProvider(self.evidence, _release=asyncio.Event())
        app = await self.app()
        task = await app.create_task("超时")
        with self.assertRaises(ValueError):
            _ = await app.gateway.submit_action(task.task_id, "speak", {"text": ""})
        action = await app.gateway.submit_action(
            task.task_id, "speak", {"text": "你好"}, timeout_s=0.02
        )
        async with asyncio.timeout(2):
            result = await app.gateway.wait_for_action(task.task_id, action.action_id)
        self.assertEqual(result.status, ActionStatus.TIMED_OUT)
        self.assertEqual(self.player.play_count, 0)
        self.assertFalse(self.device.busy)

    async def test_motion_then_reply_speech_share_runtime(self) -> None:
        """移动成功后回答播报沿用同一队列，语音不伪造到达证据."""
        self.model = FakeMessagesListChatModel(
            responses=[
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "submit_action",
                            "id": "turn",
                            "args": {
                                "skill": "turn_relative",
                                "arguments": {"angle_rad": 0.01},
                            },
                        }
                    ],
                ),
                AIMessage("模拟转向已完成，请注意热水安全。"),
            ]
        )
        app = await self.app()
        task = await app.create_task("移动讲解")
        _ = app.gateway.submit_text(task.task_id, "左转后讲解")
        with patch.object(
            FakeMessagesListChatModel, "bind_tools", return_value=self.model
        ):
            reply = await app.process_next()
        records = app.runtime.list_actions(task.task_id)
        self.assertEqual(
            [r.raw_request.skill_name for r in records], ["turn_relative", "speak"]
        )
        self.assertTrue(all(r.status == ActionStatus.SUCCEEDED for r in records))
        self.assertEqual(len(reply.action_ids), 2)
        assert records[0].ended_at is not None and records[1].started_at is not None
        self.assertLessEqual(records[0].ended_at, records[1].started_at)

    async def test_cancel_other_task_preserves_active_recording(self) -> None:
        """任务资源按归属取消，不释放其他任务的麦克风租约."""
        app = await self.app()
        first = await app.create_task("闲置任务")
        second = await app.create_task("正在录音")
        await app.start_recording(second.task_id, "input")
        _ = await app.cancel_task(first.task_id)
        self.assertTrue(self.device.busy)
        assert app.speech_input is not None
        self.assertEqual(app.speech_input.get_state("input"), "recording")
        _ = await app.cancel_task(second.task_id)
        self.assertFalse(self.device.busy)

    async def test_stop_failure_blocks_runtime(self) -> None:
        """无法确认扬声器停止时任务失败且 Runtime 禁止后续动作."""
        self.tts = MockTTSProvider(self.evidence, _duration_s=10)
        player = UnconfirmedPlayer(self.evidence, self.device)
        self.player = player
        app = await self.app(close_error="Speaker stop unconfirmed")
        task = await app.create_task("停止故障")
        action = await app.gateway.submit_action(
            task.task_id, "speak", {"text": "你好"}
        )
        try:
            async with asyncio.timeout(2):
                _ = await player.started.wait()
                state = await app.cancel_task(task.task_id)
            self.assertEqual(state.status, TaskStatus.FAILED)
            self.assertEqual(
                app.runtime.get_action(action.action_id).status, ActionStatus.FAILED
            )
            self.assertTrue(app.runtime.blocked_reason)
            with self.assertRaises(RuntimeError):
                _ = await app.runtime.submit_action(
                    app.runtime.get_action(action.action_id).raw_request
                )
        finally:
            player.fail_stop = False
