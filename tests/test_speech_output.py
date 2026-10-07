# tests/test_speech_output.py
"""验证模拟合成、播放完成、互斥占用和取消清理."""

import asyncio
import io
import unittest
import wave
from dataclasses import replace
from typing import override

from domain.services import AudioReference, PlaybackState, PlaybackStatus
from providers.audio.mock import MockAudioRecorder
from providers.audio.player import MockAudioPlayer
from providers.tts.mock import MockTTSProvider
from speech.output import SpeechOutputService
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


class LateTTSProvider(MockTTSProvider):
    """模拟取消后仍返回音频的外部服务，验证迟到结果隔离."""

    def __init__(self, _evidence: EvidenceStore) -> None:
        """初始化进入与释放信号."""
        super().__init__(_evidence)
        self.waiting: asyncio.Event = asyncio.Event()
        self.release: asyncio.Event = asyncio.Event()

    @override
    async def synthesize(
        self, text: str, voice_id: str | None, timeout_s: float
    ) -> AudioReference:
        """仅测试替身故意在取消后继续返回结果."""
        self.waiting.set()
        try:
            _ = await self.release.wait()
        except asyncio.CancelledError:
            _ = await self.release.wait()
        return await super().synthesize(text, voice_id, timeout_s)


class GatedStopPlayer(MockAudioPlayer):
    """模拟需要等待确认的播放停止."""

    def __init__(self, _evidence: EvidenceStore, _device: AudioDeviceLease) -> None:
        """初始化停止确认门."""
        super().__init__(_evidence, _device, _time_scale=100)
        self.stopping: asyncio.Event = asyncio.Event()
        self.release: asyncio.Event = asyncio.Event()

    @override
    async def stop(self, playback_id: str) -> PlaybackState:
        """等待测试允许后才确认停止."""
        self.stopping.set()
        _ = await self.release.wait()
        return await super().stop(playback_id)


class FailedStopPlayer(MockAudioPlayer):
    """模拟无法确认停止的播放器."""

    def __init__(self, _evidence: EvidenceStore, _device: AudioDeviceLease) -> None:
        """注入一次可恢复的停止故障."""
        super().__init__(_evidence, _device, _time_scale=100)
        self.fail_stop: bool = True

    @override
    async def stop(self, playback_id: str) -> PlaybackState:
        """故障期间明确抛错，不伪造停止终态."""
        if self.fail_stop:
            raise RuntimeError("Stop not confirmed")
        return await super().stop(playback_id)


class SpeechOutputTests(unittest.IsolatedAsyncioTestCase):
    """测试只使用内存证据和模拟时钟等待，不访问音频硬件."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """配置每个测试独立的语音输出依赖."""
        super().__init__(_method_name)
        self.evidence: EvidenceStore = EvidenceStore()
        self.device: AudioDeviceLease = AudioDeviceLease()
        self.tts: MockTTSProvider = MockTTSProvider(self.evidence)
        self.player: MockAudioPlayer = MockAudioPlayer(self.evidence, self.device)
        self.service: SpeechOutputService = SpeechOutputService(self.tts, self.player)
        self.baseline: set[asyncio.Task[object]] = set()

    @override
    async def asyncSetUp(self) -> None:
        """记录任务基线，异常挂起测试使用有限测试期限."""
        self.baseline = set(asyncio.all_tasks())

    @override
    async def asyncTearDown(self) -> None:
        """兜底关闭服务，关键释放断言在各测试中提前执行."""
        await self.service.close()
        self.assertFalse(self.device.busy)
        remaining = asyncio.all_tasks() - {asyncio.current_task()}
        self.assertFalse(remaining - self.baseline)

    def assert_released(self) -> None:
        """在兜底关闭之前验证设备和后台任务均已释放."""
        self.assertFalse(self.device.busy)
        remaining = asyncio.all_tasks() - {asyncio.current_task()}
        self.assertFalse(remaining - self.baseline)

    async def test_tts_wav_and_voice(self) -> None:
        """合成返回真实 WAV 元数据并传递音色，但不自动播放."""
        audio = await self.tts.synthesize("你好", "voice-test", 1)
        self.assertEqual(self.tts.last_voice_id, "voice-test")
        self.assertEqual(self.tts.call_count, 1)
        self.assertEqual(self.player.play_count, 0)
        with wave.open(
            io.BytesIO(await self.evidence.read(audio.evidence_id)), "rb"
        ) as wav:
            self.assertEqual(wav.getframerate(), audio.sample_rate_hz)
            self.assertEqual(wav.getnchannels(), audio.channels)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getnframes() / wav.getframerate(), audio.duration_s)
        self.assertEqual(self.evidence.get(audio.evidence_id).media_type, "audio/wav")
        self.assert_released()

    async def test_complete_output(self) -> None:
        """合成后等待播放完成，再返回有结束时间的终态."""
        await self.service.start("one", "你好", "voice-test")
        _ = await self.player.started.wait()
        playing = await self.player.get_state("one")
        self.assertEqual(playing.status, PlaybackStatus.PLAYING)
        self.assertIsNone(playing.ended_at)
        self.assertTrue(self.device.busy)
        final = await self.service.wait_finished("one")
        self.assertEqual(final.status, PlaybackStatus.COMPLETED)
        self.assertIsNotNone(final.ended_at)
        self.assertEqual(self.service.get_stage("one"), "completed")
        self.assertEqual(self.tts.call_count, 1)
        self.assertEqual(self.player.play_count, 1)
        self.assertEqual(await self.service.stop("one"), final)
        self.assert_released()

    async def test_stop_playing(self) -> None:
        """中途停止得到 stopped，重复停止不重新播放."""
        await self.service.start("one", "你好")
        _ = await self.player.started.wait()
        first, second = await asyncio.gather(
            self.service.stop("one"), self.service.stop("one")
        )
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.status, PlaybackStatus.STOPPED)
        self.assertIsNotNone(first.ended_at)
        self.assertEqual(first, second)
        self.assertEqual(self.player.play_count, 1)
        with self.assertRaises(asyncio.CancelledError):
            _ = await self.service.wait_finished("one")
        self.assert_released()

    async def test_cancel_synthesis(self) -> None:
        """合成期间取消，不伪造播放终态也不启动播放."""
        release = asyncio.Event()
        self.tts = MockTTSProvider(self.evidence, _release=release)
        self.service = SpeechOutputService(self.tts, self.player)
        await self.service.start("one", "你好")
        _ = await self.tts.entered.wait()
        self.assertIsNone(await self.service.stop("one"))
        release.set()
        self.assertEqual(self.player.play_count, 0)
        self.assertEqual(self.service.get_stage("one"), "cancelled")
        self.assert_released()

    async def test_late_synthesis_never_plays(self) -> None:
        """提供者迟到返回也必须被内部取消标志拦截."""
        tts = LateTTSProvider(self.evidence)
        self.service = SpeechOutputService(tts, self.player)
        await self.service.start("one", "你好")
        _ = await tts.waiting.wait()
        stopping = asyncio.create_task(self.service.stop("one"))
        try:
            await asyncio.sleep(0)
            tts.release.set()
            self.assertIsNone(await stopping)
            self.assertEqual(self.player.play_count, 0)
            self.assertEqual(self.service.get_stage("one"), "cancelled")
            self.assert_released()
        finally:
            tts.release.set()
            _ = await asyncio.gather(stopping, return_exceptions=True)

    async def test_tts_failure(self) -> None:
        """合成失败保留原异常，不变成无声成功."""
        error = RuntimeError("Injected synthesis failure")
        self.tts = MockTTSProvider(self.evidence, _error=error)
        self.service = SpeechOutputService(self.tts, self.player)
        await self.service.start("one", "你好")
        with self.assertRaises(RuntimeError) as raised:
            _ = await self.service.wait_finished("one")
        self.assertIs(raised.exception, error)
        self.assertEqual(self.service.get_stage("one"), "failed")
        self.assertEqual(self.player.play_count, 0)
        self.assert_released()

    async def test_synthesis_timeout(self) -> None:
        """合成超时明确失败，清理后无后台任务."""
        self.tts = MockTTSProvider(self.evidence, _delay_s=10)
        self.service = SpeechOutputService(self.tts, self.player)
        await self.service.start("one", "你好", timeout_s=0.001)
        with self.assertRaises(TimeoutError):
            _ = await self.service.wait_finished("one")
        self.assertEqual(self.player.play_count, 0)
        self.assertEqual(self.service.get_stage("one"), "failed")
        self.assert_released()

    async def test_playback_timeout_stops_player(self) -> None:
        """整体期限覆盖播放，超时返回前确认停止."""
        await self.service.start("one", "你好", timeout_s=0.03)
        with self.assertRaises(TimeoutError):
            _ = await self.service.wait_finished("one")
        self.assertEqual(
            (await self.player.get_state("one")).status, PlaybackStatus.STOPPED
        )
        self.assertEqual(self.service.get_stage("one"), "failed")
        self.assert_released()

    async def test_playback_failure(self) -> None:
        """播放失败反馈 failed 及错误，不报告 completed."""
        self.player = MockAudioPlayer(
            self.evidence, self.device, _error=RuntimeError("Broken output")
        )
        self.service = SpeechOutputService(self.tts, self.player)
        await self.service.start("one", "你好")
        state = await self.service.wait_finished("one")
        self.assertEqual(state.status, PlaybackStatus.FAILED)
        self.assertEqual(state.error, "Broken output")
        self.assertEqual(self.service.get_stage("one"), "failed")
        self.assert_released()

    async def test_close_during_playback(self) -> None:
        """关闭等待确认停止，重复关闭安全并拒绝新输出."""
        await self.service.start("one", "你好")
        _ = await self.player.started.wait()
        await self.service.close()
        await self.service.close()
        self.assertEqual(
            (await self.player.get_state("one")).status, PlaybackStatus.STOPPED
        )
        with self.assertRaises(RuntimeError):
            await self.service.start("two", "你好")
        self.assert_released()

    async def test_close_during_synthesis(self) -> None:
        """关闭取消未完成合成，释放后仍不会播放."""
        release = asyncio.Event()
        self.tts = MockTTSProvider(self.evidence, _release=release)
        self.service = SpeechOutputService(self.tts, self.player)
        await self.service.start("one", "你好")
        _ = await self.tts.entered.wait()
        await self.service.close()
        release.set()
        self.assertEqual(self.player.play_count, 0)
        self.assert_released()

    async def test_cancelled_close_waits_for_stop_confirmation(self) -> None:
        """关闭调用方重复取消后，仍先确认停止并释放占用."""
        player = GatedStopPlayer(self.evidence, self.device)
        self.player = player
        self.service = SpeechOutputService(self.tts, player)
        await self.service.start("one", "你好")
        _ = await player.started.wait()
        closing = asyncio.create_task(self.service.close())
        try:
            _ = await player.stopping.wait()
            for _ in range(2):
                _ = closing.cancel()
                await asyncio.sleep(0)
            self.assertFalse(closing.done())
            self.assertTrue(self.device.busy)
            player.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await closing
            self.assertEqual(
                (await player.get_state("one")).status, PlaybackStatus.STOPPED
            )
            self.assert_released()
        finally:
            player.release.set()
            _ = await asyncio.gather(closing, return_exceptions=True)

    async def test_external_wait_cancellation(self) -> None:
        """外部结果等待被取消后停止播放器并释放资源."""
        await self.service.start("one", "你好")
        _ = await self.player.started.wait()
        waiting = asyncio.create_task(self.service.wait_finished("one"))
        await asyncio.sleep(0)
        _ = waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            _ = await waiting
        self.assertEqual(
            (await self.player.get_state("one")).status, PlaybackStatus.STOPPED
        )
        self.assert_released()

    async def test_recording_playback_exclusion(self) -> None:
        """共享占用对象使录音播放双向互斥，拒绝操作不影响已有资源."""
        recorder = MockAudioRecorder(self.evidence, self.device)
        audio = await self.tts.synthesize("你好", None, 1)
        try:
            await recorder.start("recording", 10)
            with self.assertRaises(RuntimeError):
                await self.player.play("blocked", audio)
            self.assertTrue(self.device.busy)
            await recorder.cancel("recording")
            await self.player.play("playing", audio)
            with self.assertRaises(RuntimeError):
                await recorder.start("blocked", 10)
            self.assertEqual(
                (await self.player.get_state("playing")).status, PlaybackStatus.PLAYING
            )
            _ = await self.player.stop("playing")
            await recorder.start("released", 10)
            await recorder.cancel("released")
            self.assert_released()
        finally:
            await recorder.close()

    async def test_invalid_audio(self) -> None:
        """损坏音频、错误元数据和不存在证据均不占用设备."""
        audio = await self.tts.synthesize("你好", None, 1)
        bad = await self.evidence.save(b"not wav", "audio/wav", 1)
        for candidate in [
            replace(audio, duration_s=2),
            replace(audio, evidence_id=bad.evidence_id),
        ]:
            with self.assertRaises(ValueError):
                await self.player.play("one", candidate)
        with self.assertRaises(KeyError):
            await self.player.play("one", replace(audio, evidence_id="missing"))
        self.assertEqual(self.player.play_count, 0)
        self.assert_released()

    async def test_duplicate_and_busy_output(self) -> None:
        """重复编号和活动输出拒绝新合成，调用次数保持一次."""
        await self.service.start("one", "你好")
        with self.assertRaises(ValueError):
            await self.service.start("one", "重复")
        with self.assertRaises(RuntimeError):
            await self.service.start("two", "并发")
        _ = await self.service.wait_finished("one")
        self.assertEqual(self.tts.call_count, 1)
        self.assertEqual(self.player.play_count, 1)
        self.assert_released()

    async def test_invalid_parameters(self) -> None:
        """非法文本、音色及期限在创建后台任务前被拒绝."""
        with self.assertRaises(ValueError):
            await self.service.start("one", " ")
        with self.assertRaises(ValueError):
            await self.service.start("one", "你好", voice_id=" ")
        for timeout in [0, -1, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                await self.service.start("one", "你好", timeout_s=timeout)
        self.assertEqual(self.tts.call_count, 0)
        self.assert_released()

    async def test_stop_failure_is_not_success(self) -> None:
        """停止无法确认时保留失败语义，不报告 stopped 或 completed."""
        player = FailedStopPlayer(self.evidence, self.device)
        self.player = player
        self.service = SpeechOutputService(self.tts, player)
        await self.service.start("one", "你好")
        _ = await player.started.wait()
        try:
            with self.assertRaisesRegex(RuntimeError, "Stop not confirmed"):
                _ = await self.service.stop("one")
            self.assertEqual(self.service.get_stage("one"), "failed")
            self.assertEqual(
                (await player.get_state("one")).status, PlaybackStatus.PLAYING
            )
            self.assertTrue(self.device.busy)
        finally:
            player.fail_stop = False
            _ = await player.stop("one")
        # 服务保留这次清理失败，关闭不会将已失败的停止伪装为成功。
        with self.assertRaisesRegex(RuntimeError, "Stop not confirmed"):
            await self.service.close()
        self.assert_released()
        self.service = SpeechOutputService(self.tts, MockAudioPlayer(self.evidence))

    async def test_direct_player_close_cancellation(self) -> None:
        """直接关闭播放器的调用被取消，返回前也已确认停止."""
        audio = await self.tts.synthesize("你好", None, 1)
        await self.player.play("one", audio)
        closing = asyncio.create_task(self.player.close())
        await asyncio.sleep(0)
        _ = closing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        self.assertEqual(
            (await self.player.get_state("one")).status, PlaybackStatus.STOPPED
        )
        self.assert_released()

    async def test_direct_player_wait_cancellation(self) -> None:
        """直接取消播放结果等待时也要等待停止确认."""
        audio = await self.tts.synthesize("你好", None, 1)
        await self.player.play("one", audio)
        waiting = asyncio.create_task(self.player.wait_finished("one"))
        await asyncio.sleep(0)
        _ = waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            _ = await waiting
        self.assertEqual(
            (await self.player.get_state("one")).status, PlaybackStatus.STOPPED
        )
        self.assert_released()
