# tests/test_speech_input.py
"""验证内存语音输入闭环、单次完成和取消后的资源释放."""

import asyncio
import io
import unittest
import wave
from typing import override

from app.team_gateway import TeamGateway, UserInput
from domain.services import AudioReference, EvidenceReference, TranscriptResult
from perception.simulated import SimulatedPerception
from providers.asr.mock import MockASRProvider
from providers.audio.mock import MockAudioRecorder
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.registry import SkillRegistry
from speech.input import SpeechInputService
from storage.evidence import EvidenceStore


class GatedEvidenceStore(EvidenceStore):
    """固定音频保存期间的并发结束和取消时序."""

    def __init__(self) -> None:
        """创建保存门和调用计数."""
        super().__init__()
        self.entered: asyncio.Event = asyncio.Event()
        self.allow_save: asyncio.Event = asyncio.Event()
        self.save_count: int = 0

    @override
    async def save(
        self, content: bytes, media_type: str, captured_at: float
    ) -> EvidenceReference:
        """在实际保存前等待测试释放."""
        self.save_count += 1
        self.entered.set()
        _ = await self.allow_save.wait()
        return await super().save(content, media_type, captured_at)


class GatedCleanupRecorder(MockAudioRecorder):
    """在清理期间等待，用于验证重复取消不会中断释放."""

    def __init__(self, _evidence: EvidenceStore) -> None:
        """初始化清理控制信号."""
        super().__init__(_evidence)
        self.cleaning: asyncio.Event = asyncio.Event()
        self.release_cleanup: asyncio.Event = asyncio.Event()
        self.closed: bool = False

    @override
    async def cancel(self, recording_id: str) -> None:
        """允许测试在资源清理未完成时取消外部等待者."""
        self.cleaning.set()
        _ = await self.release_cleanup.wait()
        await super().cancel(recording_id)

    @override
    async def close(self) -> None:
        """标记录音器实际完成关闭."""
        await super().close()
        self.closed = True


class LateASR(MockASRProvider):
    """模拟捕获取消后仍交回文本的外部识别适配器."""

    @override
    async def transcribe(
        self, audio: AudioReference, timeout_s: float
    ) -> TranscriptResult:
        """等待取消后返回最终文本，测试宿主的结果提交屏障."""
        self.entered.set()
        try:
            _ = await asyncio.Event().wait()
        except asyncio.CancelledError:
            return TranscriptResult("迟到的移动指令")
        raise AssertionError("Unreachable")


class SpeechInputTests(unittest.IsolatedAsyncioTestCase):
    """所有测试仅用标准库和内存服务，不访问设备或网络."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """创建测试依赖，不在构造期间启动后台任务."""
        super().__init__(_method_name)
        self.baseline: set[asyncio.Task[object]] = set()
        self.evidence: EvidenceStore = EvidenceStore()
        self.recorder: MockAudioRecorder = MockAudioRecorder(self.evidence)
        self.asr: MockASRProvider = MockASRProvider()
        self.runtime: ActionManager = ActionManager(SimulatedAdapter(), SkillRegistry())
        self.gateway: TeamGateway = TeamGateway(
            self.runtime, SimulatedPerception(self.evidence)
        )
        self.task_id: int = self.gateway.create_task("语音输入测试").task_id
        self.service: SpeechInputService = SpeechInputService(
            self.recorder, self.asr, self.gateway
        )

    async def test_cancel_rejects_late_asr_result(self) -> None:
        """识别吞掉取消后返回文本，取消会话也不能重新入队."""
        asr = LateASR()
        self.service = SpeechInputService(self.recorder, asr, self.gateway)
        await self.service.start(self.task_id, "late", 0.001)
        async with asyncio.timeout(1):
            _ = await asr.entered.wait()
            await self.service.cancel("late")
        self.assertEqual(self.service.get_state("late"), "cancelled")
        with self.assertRaises(asyncio.CancelledError):
            _ = await self.service.wait_finished("late")
        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(0.01):
                _ = await self.gateway.next_input()

    async def test_timeout_rejects_late_asr_result(self) -> None:
        """识别吞掉期限取消也必须报超时且不提交文本."""
        asr = LateASR()
        self.service = SpeechInputService(self.recorder, asr, self.gateway)
        await self.service.start(self.task_id, "late", 0.001, timeout_s=0.01)
        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(1):
                _ = await self.service.wait_finished("late")
        self.assertEqual(self.service.get_state("late"), "failed")
        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(0.01):
                _ = await self.gateway.next_input()

    async def test_close_rejects_late_asr_result(self) -> None:
        """服务关闭后返回的识别结果也不得产生新输入."""
        asr = LateASR()
        self.service = SpeechInputService(self.recorder, asr, self.gateway)
        await self.service.start(self.task_id, "late", 0.001)
        async with asyncio.timeout(1):
            _ = await asr.entered.wait()
            await self.service.close()
        self.assertEqual(self.service.get_state("late"), "cancelled")
        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(0.01):
                _ = await self.gateway.next_input()

    @override
    async def asyncSetUp(self) -> None:
        """记录后台任务基线并显式启动模拟运行时."""
        self.baseline = set(asyncio.all_tasks())
        await self.runtime.start()

    @override
    async def asyncTearDown(self) -> None:
        """关闭全部服务并核对无遗留任务."""
        await self.service.close()
        await self.runtime.close()
        await asyncio.sleep(0)
        remaining = asyncio.all_tasks() - {asyncio.current_task()}
        self.assertFalse(remaining - self.baseline)

    def configure_asr(self, asr: MockASRProvider) -> None:
        """在开始会话前替换测试识别器."""
        self.asr = asr
        self.service = SpeechInputService(self.recorder, asr, self.gateway)

    async def assert_no_input(self) -> None:
        """核对核心队列没有输入，不能只检查返回值."""
        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(0.01):
                _ = await self.gateway.next_input()

    async def test_audio_reference_and_wav(self) -> None:
        """录音生成唯一编号和可解析的 WAV，重复结束返回同一引用."""
        await self.recorder.start("one", 10)
        audio = await self.recorder.finish("one")
        self.assertEqual(audio, await self.recorder.finish("one"))
        self.assertEqual(self.recorder.finalize_count, 1)
        metadata = self.evidence.get(audio.evidence_id)
        self.assertEqual(metadata.media_type, "audio/wav")
        with wave.open(
            io.BytesIO(await self.evidence.read(audio.evidence_id)), "rb"
        ) as wav:
            self.assertEqual(wav.getframerate(), audio.sample_rate_hz)
            self.assertEqual(wav.getnchannels(), audio.channels)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getnframes() / wav.getframerate(), audio.duration_s)
        await self.recorder.start("two", 10)
        other = await self.recorder.finish("two")
        self.assertNotEqual(audio.audio_id, other.audio_id)
        self.assertNotEqual(audio.evidence_id, other.evidence_id)

    async def test_manual_finish_submits_once(self) -> None:
        """主动结束生成一次音频、一次识别和一个核心输入."""
        await self.service.start(self.task_id, "one", 10)
        first, second = await asyncio.gather(
            self.service.finish("one"), self.service.finish("one")
        )
        self.assertIsInstance(first, UserInput)
        self.assertEqual(first, second)
        self.assertEqual(await self.gateway.next_input(), first)
        self.assertEqual(self.service.get_state("one"), "completed")
        self.assertEqual(self.recorder.finalize_count, 1)
        self.assertEqual(self.asr.call_count, 1)
        await self.assert_no_input()

    async def test_auto_finish(self) -> None:
        """最长时长自动触发识别，无需手动结束."""
        await self.service.start(self.task_id, "one", 0.001)
        item = await self.service.wait_finished("one")
        self.assertEqual(await self.gateway.next_input(), item)
        self.assertEqual(self.service.get_state("one"), "completed")
        self.assertEqual(self.recorder.finalize_count, 1)
        self.assertEqual(self.asr.call_count, 1)

    async def test_auto_manual_race(self) -> None:
        """自动结束与主动结束交错，核对所有完成调用次数."""
        await self.service.start(self.task_id, "one", 0.001)
        # 用识别进入信号确认自动结束已发生，随后并发请求主动结束。
        _ = await self.asr.entered.wait()
        first, second = await asyncio.gather(
            self.service.finish("one"), self.service.wait_finished("one")
        )
        self.assertEqual(first, second)
        self.assertEqual(await self.gateway.next_input(), first)
        self.assertEqual(self.recorder.finalize_count, 1)
        self.assertEqual(self.asr.call_count, 1)
        self.assertEqual(self.service.get_state("one"), "completed")
        await self.assert_no_input()

    async def test_empty_and_nonfinal(self) -> None:
        """空文本和非最终转写均不调用核心提交方法."""
        for index, result in enumerate(
            [TranscriptResult("  "), TranscriptResult("中间结果", is_final=False)]
        ):
            with self.subTest(result=result):
                self.configure_asr(MockASRProvider(_result=result))
                # 录音成功启动后取消任务，验证空结果不会调用提交门面。
                task_id = self.gateway.create_task("无有效输入").task_id
                recording_id = str(index)
                await self.service.start(task_id, recording_id, 10)
                _ = await self.gateway.cancel_task(task_id)
                self.assertIsNone(await self.service.finish(recording_id))
                self.assertEqual(self.service.get_state(recording_id), "completed")
                self.assertEqual(self.asr.call_count, 1)
                await self.service.close()
                self.recorder = MockAudioRecorder(self.evidence)
        await self.assert_no_input()

    async def test_asr_failure(self) -> None:
        """识别失败保留原始异常，资源释放且不提交输入."""
        error = RuntimeError("Injected ASR failure")
        self.configure_asr(MockASRProvider(_error=error))
        await self.service.start(self.task_id, "one", 10)
        with self.assertRaises(RuntimeError) as raised:
            _ = await self.service.finish("one")
        self.assertIs(raised.exception, error)
        self.assertEqual(self.service.get_state("one"), "failed")
        self.assertEqual(self.asr.call_count, 1)
        self.assertEqual(self.recorder.finalize_count, 1)
        await self.assert_no_input()

    async def test_asr_timeout(self) -> None:
        """识别期限到达明确失败，无输入和遗留录音任务."""
        self.configure_asr(MockASRProvider(_delay_s=10))
        await self.service.start(self.task_id, "one", 10, timeout_s=0.001)
        with self.assertRaises(TimeoutError):
            _ = await self.service.finish("one")
        self.assertEqual(self.service.get_state("one"), "failed")
        self.assertEqual(self.asr.call_count, 1)
        await self.assert_no_input()

    async def test_gateway_duplicate_and_conflict(self) -> None:
        """跨调用去重仍由门面处理，不创建第二个输入编号."""
        await self.service.start(self.task_id, "one", 10)
        item = await self.service.finish("one")
        duplicate = self.gateway.submit_transcript(
            self.task_id, "one", TranscriptResult("模拟识别文本")
        )
        self.assertEqual(item, duplicate)
        self.assertEqual(await self.gateway.next_input(), item)
        with self.assertRaises(ValueError):
            _ = self.gateway.submit_transcript(
                self.task_id, "one", TranscriptResult("不同文本")
            )
        await self.assert_no_input()

    async def test_cancelled_task_late_asr(self) -> None:
        """通过事件固定任务取消与迟到识别，最终由门面拒绝."""
        release = asyncio.Event()
        self.configure_asr(MockASRProvider(_release=release))
        await self.service.start(self.task_id, "one", 0.001)
        _ = await self.asr.entered.wait()
        _ = await self.gateway.cancel_task(self.task_id)
        release.set()
        with self.assertRaisesRegex(ValueError, "Task no longer accepts input"):
            _ = await self.service.wait_finished("one")
        self.assertEqual(self.service.get_state("one"), "failed")
        self.assertEqual(self.asr.call_count, 1)
        await self.assert_no_input()

    async def test_cancel_recording(self) -> None:
        """取消正在录音的会话，不生成音频或调用识别."""
        await self.service.start(self.task_id, "one", 10)
        await self.service.cancel("one")
        with self.assertRaises(asyncio.CancelledError):
            _ = await self.service.wait_finished("one")
        self.assertEqual(self.service.get_state("one"), "cancelled")
        self.assertEqual(self.recorder.finalize_count, 0)
        self.assertEqual(self.asr.call_count, 0)
        await self.assert_no_input()

    async def test_close_service(self) -> None:
        """关闭阻止迟到录音结果和新会话，重复关闭安全."""
        await self.service.start(self.task_id, "one", 10)
        await self.service.close()
        await self.service.close()
        self.assertEqual(self.service.get_state("one"), "cancelled")
        self.assertEqual(self.recorder.finalize_count, 0)
        self.assertEqual(self.asr.call_count, 0)
        with self.assertRaises(RuntimeError):
            await self.service.start(self.task_id, "two", 10)
        with self.assertRaises(RuntimeError):
            await self.recorder.start("two", 10)

    async def test_external_wait_cancellation(self) -> None:
        """外部等待取消立即清理录音，不保留后台作业."""
        await self.service.start(self.task_id, "one", 10)
        waiting = asyncio.create_task(self.service.wait_finished("one"))
        await asyncio.sleep(0)
        _ = waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            _ = await waiting
        self.assertEqual(self.service.get_state("one"), "cancelled")
        self.assertEqual(self.recorder.finalize_count, 0)
        self.assertEqual(self.asr.call_count, 0)

    async def test_cancel_during_asr(self) -> None:
        """识别期间关闭服务后，即使释放识别门也不会提交输入."""
        release = asyncio.Event()
        self.configure_asr(MockASRProvider(_release=release))
        await self.service.start(self.task_id, "one", 0.001)
        _ = await self.asr.entered.wait()
        await self.service.close()
        release.set()
        self.assertEqual(self.service.get_state("one"), "cancelled")
        self.assertEqual(self.asr.call_count, 1)
        await self.assert_no_input()

    async def test_invalid_parameters_and_duplicate_session(self) -> None:
        """非法期限和重用编号在创建额外作业前被拒绝."""
        for duration in [0, -1, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                await self.service.start(self.task_id, "bad", duration)
        await self.service.start(self.task_id, "one", 10)
        with self.assertRaises(ValueError):
            await self.service.start(self.task_id, "one", 10)
        with self.assertRaises(RuntimeError):
            await self.recorder.start("two", 10)

    async def test_evidence_failure(self) -> None:
        """保存失败不进入识别，并释放录音后台任务."""
        await self.service.close()
        self.recorder = MockAudioRecorder(EvidenceStore(_max_bytes=1))
        self.service = SpeechInputService(self.recorder, self.asr, self.gateway)
        await self.service.start(self.task_id, "one", 0.001)
        with self.assertRaises(ValueError):
            _ = await self.service.wait_finished("one")
        self.assertEqual(self.service.get_state("one"), "failed")
        self.assertEqual(self.asr.call_count, 0)

    async def test_finish_while_auto_finalizing(self) -> None:
        """在自动结束保存尚未完成时主动结束，保存和识别均只一次."""
        await self.service.close()
        store = GatedEvidenceStore()
        self.recorder = MockAudioRecorder(store)
        self.service = SpeechInputService(self.recorder, self.asr, self.gateway)
        await self.service.start(self.task_id, "one", 0.001)
        _ = await store.entered.wait()
        finishing = asyncio.create_task(self.service.finish("one"))
        await asyncio.sleep(0)
        store.allow_save.set()
        item = await finishing
        self.assertEqual(await self.gateway.next_input(), item)
        self.assertEqual(store.save_count, 1)
        self.assertEqual(self.recorder.finalize_count, 1)
        self.assertEqual(self.asr.call_count, 1)
        self.assertEqual(self.service.get_state("one"), "completed")
        await self.assert_no_input()

    async def test_close_during_finalize(self) -> None:
        """关闭打断尚未完成的保存，释放后不会产生结果."""
        await self.service.close()
        store = GatedEvidenceStore()
        self.recorder = MockAudioRecorder(store)
        self.service = SpeechInputService(self.recorder, self.asr, self.gateway)
        await self.service.start(self.task_id, "one", 0.001)
        _ = await store.entered.wait()
        await self.service.close()
        store.allow_save.set()
        self.assertEqual(self.recorder.finalize_count, 0)
        self.assertEqual(self.asr.call_count, 0)
        self.assertEqual(self.service.get_state("one"), "cancelled")
        await self.assert_no_input()

    async def test_direct_recorder_wait_cancellation(self) -> None:
        """直接取消录音等待者也要终止内部录音任务."""
        await self.recorder.start("one", 10)
        waiting = asyncio.create_task(self.recorder.wait_finished("one"))
        await asyncio.sleep(0)
        _ = waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            _ = await waiting
        self.assertEqual(self.recorder.finalize_count, 0)
        with self.assertRaises(asyncio.CancelledError):
            _ = await self.recorder.finish("one")

    async def test_external_finish_cancellation_during_asr(self) -> None:
        """取消主动结束的等待者同时取消正在进行的识别."""
        release = asyncio.Event()
        self.configure_asr(MockASRProvider(_release=release))
        await self.service.start(self.task_id, "one", 10)
        finishing = asyncio.create_task(self.service.finish("one"))
        _ = await self.asr.entered.wait()
        _ = finishing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            _ = await finishing
        release.set()
        self.assertEqual(self.service.get_state("one"), "cancelled")
        self.assertEqual(self.asr.call_count, 1)
        await self.assert_no_input()

    async def test_cancelled_close_cleans_all_sessions_before_return(self) -> None:
        """关闭等待者反复取消后，返回前全部资源已释放，无需补救关闭."""
        await self.service.close()
        recorder = GatedCleanupRecorder(self.evidence)
        asr = MockASRProvider(_release=asyncio.Event())
        self.recorder = recorder
        self.configure_asr(asr)
        baseline = set(asyncio.all_tasks())
        await self.service.start(self.task_id, "one", 0.001)
        _ = await asr.entered.wait()
        await self.service.start(self.task_id, "two", 10)
        closing = asyncio.create_task(self.service.close())
        try:
            _ = await recorder.cleaning.wait()
            for _ in range(2):
                _ = closing.cancel()
                await asyncio.sleep(0)
            self.assertFalse(closing.done())
            recorder.release_cleanup.set()
            with self.assertRaises(asyncio.CancelledError):
                await closing
            self.assertTrue(recorder.closed)
            self.assertEqual(self.service.get_state("one"), "cancelled")
            self.assertEqual(self.service.get_state("two"), "cancelled")
            self.assertEqual(asr.call_count, 1)
            self.assertEqual(recorder.finalize_count, 1)
            self.assertFalse(set(asyncio.all_tasks()) - baseline)
            await self.assert_no_input()
        finally:
            recorder.release_cleanup.set()
            _ = await asyncio.gather(closing, return_exceptions=True)

    async def test_concurrent_cancel_waits_for_same_cleanup(self) -> None:
        """并发取消等待同一次清理，取消调用方不终止后台释放."""
        await self.service.close()
        recorder = GatedCleanupRecorder(self.evidence)
        self.recorder = recorder
        self.service = SpeechInputService(recorder, self.asr, self.gateway)
        baseline = set(asyncio.all_tasks())
        await self.service.start(self.task_id, "one", 10)
        await asyncio.sleep(0)
        first = asyncio.create_task(self.service.cancel("one"))
        second = asyncio.create_task(self.service.cancel("one"))
        try:
            _ = await recorder.cleaning.wait()
            _ = first.cancel()
            await asyncio.sleep(0)
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            recorder.release_cleanup.set()
            with self.assertRaises(asyncio.CancelledError):
                await first
            await second
            self.assertEqual(self.service.get_state("one"), "cancelled")
            self.assertEqual(self.asr.call_count, 0)
            self.assertEqual(recorder.finalize_count, 0)
            self.assertFalse(set(asyncio.all_tasks()) - baseline)
        finally:
            recorder.release_cleanup.set()
            _ = await asyncio.gather(first, second, return_exceptions=True)
