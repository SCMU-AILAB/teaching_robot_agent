# tests/test_speech_integration.py
"""验证输入输出在同一装配上下文的交接、互斥和独立资源所有权."""

import asyncio
import unittest
from typing import override

from app.team_gateway import TeamGateway
from domain.services import PlaybackStatus
from perception.simulated import SimulatedPerception
from providers.asr.mock import MockASRProvider
from providers.audio.mock import MockAudioRecorder
from providers.audio.player import MockAudioPlayer
from providers.tts.mock import MockTTSProvider
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.registry import SkillRegistry
from speech.input import SpeechInputService
from speech.output import SpeechOutputService
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


class SpeechIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """仅测试内部服务装配，不接管任务取消、教学决策或 Runtime 事件."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """为每个用例建立同一证据存储和同一设备租约."""
        super().__init__(_method_name)
        self.evidence: EvidenceStore = EvidenceStore()
        self.device: AudioDeviceLease = AudioDeviceLease()
        self.recorder: MockAudioRecorder = MockAudioRecorder(self.evidence, self.device)
        self.player: MockAudioPlayer = MockAudioPlayer(self.evidence, self.device)
        self.asr: MockASRProvider = MockASRProvider()
        self.tts: MockTTSProvider = MockTTSProvider(self.evidence)
        self.perception: SimulatedPerception = SimulatedPerception(self.evidence)
        self.runtime: ActionManager = ActionManager(SimulatedAdapter(), SkillRegistry())
        self.gateway: TeamGateway = TeamGateway(self.runtime, self.perception)
        self.input: SpeechInputService = SpeechInputService(
            self.recorder, self.asr, self.gateway
        )
        self.output: SpeechOutputService = SpeechOutputService(self.tts, self.player)
        self.task_id: int = self.gateway.create_task("跨服务联调").task_id
        self.baseline: set[asyncio.Task[object]] = set()

    @override
    async def asyncSetUp(self) -> None:
        """启动模拟核心后记录基线，以便在兜底关闭前检查语音作业."""
        await self.runtime.start()
        self.baseline = set(asyncio.all_tasks())
        self.addAsyncCleanup(self.runtime.close)
        self.addAsyncCleanup(self.perception.close)
        self.addAsyncCleanup(self.output.close)
        self.addAsyncCleanup(self.input.close)

    def assert_released(self) -> None:
        """在测试兜底清理前验证设备及两个服务的后台任务均释放."""
        self.assertFalse(self.device.busy)
        remaining = asyncio.all_tasks() - {asyncio.current_task()}
        self.assertFalse(remaining - self.baseline)

    async def test_two_rounds_share_lease_and_handoff_once(self) -> None:
        """两轮输入输出交替复用设备，并发结束不重复创建输入或播报."""
        for index in range(2):
            recording_id = f"recording-{index}"
            playback_id = f"playback-{index}"
            await self.input.start(self.task_id, recording_id, 15)
            first, second = await asyncio.gather(
                self.input.finish(recording_id), self.input.finish(recording_id)
            )
            self.assertIsNotNone(first)
            self.assertEqual(first, second)
            async with asyncio.timeout(1):
                self.assertEqual(await self.gateway.next_input(), first)
            with self.assertRaises(TimeoutError):
                async with asyncio.timeout(0.01):
                    _ = await self.gateway.next_input()
            await self.output.start(playback_id, "调用方提供的固定回答")
            self.assertEqual(
                (await self.output.wait_finished(playback_id)).status,
                PlaybackStatus.COMPLETED,
            )
            self.assertEqual(self.input.get_state(recording_id), "completed")
            self.assertEqual(self.recorder.finalize_count, index + 1)
            self.assertEqual(self.asr.call_count, index + 1)
            self.assertEqual(self.tts.call_count, index + 1)
            self.assertEqual(self.player.play_count, index + 1)
            self.assert_released()

    async def test_output_busy_failure_preserves_recording(self) -> None:
        """录音占用时输出明确失败，未中断录音，释放后可正常播报."""
        await self.input.start(self.task_id, "recording", 15)
        await self.output.start("blocked", "固定回答")
        with self.assertRaisesRegex(RuntimeError, "Audio device busy"):
            _ = await self.output.wait_finished("blocked")
        self.assertEqual(self.output.get_stage("blocked"), "failed")
        self.assertEqual(self.input.get_state("recording"), "recording")
        self.assertTrue(self.device.busy)
        self.assertEqual(self.player.play_count, 0)
        self.assertEqual(self.asr.call_count, 0)
        _ = await self.input.finish("recording")
        await self.output.start("retry", "固定回答")
        self.assertEqual(
            (await self.output.wait_finished("retry")).status,
            PlaybackStatus.COMPLETED,
        )
        self.assertEqual(self.tts.call_count, 2)
        self.assertEqual(self.player.play_count, 1)
        self.assertEqual(self.recorder.finalize_count, 1)
        self.assertEqual(self.asr.call_count, 1)
        self.assert_released()

    async def test_stop_playback_allows_next_recording(self) -> None:
        """播放拒绝新录音，确认停止后允许同一未成功启动的编号重试."""
        self.tts = MockTTSProvider(self.evidence, _duration_s=10)
        self.output = SpeechOutputService(self.tts, self.player)
        self.addAsyncCleanup(self.output.close)
        await self.output.start("playback", "固定回答")
        async with asyncio.timeout(1):
            _ = await self.player.started.wait()
        with self.assertRaisesRegex(RuntimeError, "Audio device busy"):
            await self.input.start(self.task_id, "recording", 15)
        self.assertEqual(
            (await self.player.get_state("playback")).status, PlaybackStatus.PLAYING
        )
        stopped = await self.output.stop("playback")
        self.assertIsNotNone(stopped)
        self.assertEqual(
            (await self.player.get_state("playback")).status, PlaybackStatus.STOPPED
        )
        await self.input.start(self.task_id, "recording", 15)
        _ = await self.input.finish("recording")
        self.assertEqual(self.recorder.finalize_count, 1)
        self.assertEqual(self.asr.call_count, 1)
        self.assertEqual(self.player.play_count, 1)
        self.assert_released()

    async def test_input_close_does_not_release_playback_owner(self) -> None:
        """旧录音关闭不可释放随后取得租约的播放器."""
        await self.input.start(self.task_id, "recording", 15)
        _ = await self.input.finish("recording")
        self.tts = MockTTSProvider(self.evidence, _duration_s=10)
        self.output = SpeechOutputService(self.tts, self.player)
        self.addAsyncCleanup(self.output.close)
        await self.output.start("playback", "固定回答")
        async with asyncio.timeout(1):
            _ = await self.player.started.wait()
        await self.input.close()
        self.assertTrue(self.device.busy)
        self.assertEqual(
            (await self.player.get_state("playback")).status, PlaybackStatus.PLAYING
        )
        await self.output.close()
        self.assertEqual(
            (await self.player.get_state("playback")).status, PlaybackStatus.STOPPED
        )
        self.assert_released()

    async def test_output_close_does_not_release_recording_owner(self) -> None:
        """旧播放关闭不可释放随后取得租约的录音器."""
        await self.output.start("playback", "固定回答")
        _ = await self.output.wait_finished("playback")
        await self.input.start(self.task_id, "recording", 15)
        await self.output.close()
        self.assertTrue(self.device.busy)
        self.assertEqual(self.input.get_state("recording"), "recording")
        self.assertEqual(self.recorder.finalize_count, 0)
        self.assertEqual(self.asr.call_count, 0)
        await self.input.cancel("recording")
        self.assertEqual(self.input.get_state("recording"), "cancelled")
        self.assert_released()

    async def test_close_both_services_during_model_waits(self) -> None:
        """识别和合成同时等待时分别关闭服务，无迟到输入或播放作业."""
        asr_release = asyncio.Event()
        tts_release = asyncio.Event()
        self.asr = MockASRProvider(_release=asr_release)
        self.tts = MockTTSProvider(self.evidence, _release=tts_release)
        self.input = SpeechInputService(self.recorder, self.asr, self.gateway)
        self.output = SpeechOutputService(self.tts, self.player)
        self.addAsyncCleanup(self.input.close)
        self.addAsyncCleanup(self.output.close)
        await self.input.start(self.task_id, "recording", 0.001)
        async with asyncio.timeout(1):
            _ = await self.asr.entered.wait()
        await self.output.start("playback", "固定回答")
        async with asyncio.timeout(1):
            _ = await self.tts.entered.wait()
        # 模型等待不占用设备；此处显式关闭两个服务，不冒充任务级取消。
        self.assertFalse(self.device.busy)
        _ = await asyncio.gather(self.input.close(), self.output.close())
        asr_release.set()
        tts_release.set()
        self.assertEqual(self.input.get_state("recording"), "cancelled")
        self.assertEqual(self.output.get_stage("playback"), "cancelled")
        self.assertEqual(self.recorder.finalize_count, 1)
        self.assertEqual(self.asr.call_count, 1)
        self.assertEqual(self.tts.call_count, 1)
        self.assertEqual(self.player.play_count, 0)
        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(0.01):
                _ = await self.gateway.next_input()
        self.assert_released()
