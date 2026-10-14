# tests/test_evidence_lifecycle.py
"""验证观察有效窗口与证据按任务、语音作业释放."""

import asyncio
import time
import unittest
from dataclasses import replace
from typing import override

from app.team_gateway import TeamGateway
from domain.models import TaskStatus
from domain.services import ObservationRequest, ObservationResult, PlaybackStatus
from perception.perception_service import LivePerception
from perception.scene_store import SceneStore
from perception.simulated import SimulatedPerception
from providers.asr.mock import MockASRProvider
from providers.audio.mock import MockAudioRecorder
from providers.audio.player import MockAudioPlayer
from providers.tts.mock import MockTTSProvider
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from runtime.task_coordinator import TaskCoordinator
from skills.registry import SkillRegistry
from speech.input import SpeechInputService
from speech.output import SpeechOutputService
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore
from tests.test_perception_service import FakeCamera, FakeProvider


class AgedPerception(SimulatedPerception):
    """把采集时间往前推，模拟真实视觉模型的推理耗时."""

    def __init__(self, _evidence: EvidenceStore, _age_s: float) -> None:
        """记录需要模拟的帧年龄."""
        super().__init__(_evidence)
        self.age_s: float = _age_s

    @override
    async def observe(self, request: ObservationRequest) -> ObservationResult:
        """返回采集时间早于当前的观察."""
        result = await super().observe(request)
        frame = replace(result.frame, captured_at=time.time() - self.age_s)
        return replace(result, frame=frame)


class SharedFramePerception(SimulatedPerception):
    """第二次观察复用第一次的帧，模拟跨任务缓存命中."""

    def __init__(self, _evidence: EvidenceStore) -> None:
        """初始化共享帧槽位."""
        super().__init__(_evidence)
        self.shared: ObservationResult | None = None

    @override
    async def observe(self, request: ObservationRequest) -> ObservationResult:
        """首次采集，之后返回同一帧."""
        if self.shared is None:
            self.shared = await super().observe(request)
            return self.shared
        return replace(
            self.shared,
            observation_id=request.observation_id,
            task_id=request.task_id,
        )


class ObservationWindowTests(unittest.IsolatedAsyncioTestCase):
    """观察只因场景变化或超过有效窗口而过期."""

    async def test_inference_latency_does_not_make_observation_stale(self) -> None:
        """采集后数秒才交给模型的观察仍在 30 秒窗口内，不能被判为过期."""
        evidence = EvidenceStore()
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(runtime, AgedPerception(evidence, 8.0))
            task = gateway.create_task("看看桌上")
            _ = await gateway.observe(ObservationRequest("o1", task.task_id, "桌上"))
            resolved = gateway.resolve_observation(task.task_id, None)
            assert resolved is not None
            self.assertFalse(resolved.stale)

    async def test_observation_older_than_window_is_stale(self) -> None:
        """超过配置窗口的观察标记过期."""
        evidence = EvidenceStore()
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(
                runtime, AgedPerception(evidence, 8.0), _observation_ttl_s=5
            )
            task = gateway.create_task("看看桌上")
            _ = await gateway.observe(ObservationRequest("o1", task.task_id, "桌上"))
            resolved = gateway.resolve_observation(task.task_id, None)
            assert resolved is not None
            self.assertTrue(resolved.stale)

    async def test_invalid_window_is_rejected(self) -> None:
        """有效窗口必须为正."""
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            for value in (0.0, -1.0, float("nan")):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    _ = TeamGateway(
                        runtime,
                        SimulatedPerception(EvidenceStore()),
                        _observation_ttl_s=value,
                    )


class TaskEvidenceTests(unittest.IsolatedAsyncioTestCase):
    """任务终态释放该任务独占的观察帧."""

    async def test_finish_and_cancel_release_task_frames(self) -> None:
        """显式验收和取消两条终态路径都释放观察帧."""
        evidence = EvidenceStore()
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(
                runtime, SimulatedPerception(evidence), _evidence=evidence
            )
            finished = gateway.create_task("验收")
            cancelled = gateway.create_task("取消")
            first = await gateway.observe(
                ObservationRequest("o1", finished.task_id, "桌上")
            )
            second = await gateway.observe(
                ObservationRequest("o2", cancelled.task_id, "桌上")
            )
            _ = gateway.finish_interaction(finished.task_id)
            with self.assertRaises(KeyError):
                _ = evidence.get(first.frame.evidence_id)
            _ = evidence.get(second.frame.evidence_id)
            _ = await gateway.cancel_task(cancelled.task_id)
            with self.assertRaises(KeyError):
                _ = evidence.get(second.frame.evidence_id)
            self.assertEqual(evidence.used_bytes, 0)
            # 观察记录保留供事后查询，只是证据已释放。
            self.assertIsNotNone(gateway.latest_observation(finished.task_id))

    async def test_frame_shared_by_active_task_is_kept(self) -> None:
        """缓存让两个任务引用同一帧时，一个任务结束不能释放另一个仍在用的帧."""
        evidence = EvidenceStore()
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(
                runtime, SharedFramePerception(evidence), _evidence=evidence
            )
            first = gateway.create_task("第一个")
            second = gateway.create_task("第二个")
            result = await gateway.observe(
                ObservationRequest("o1", first.task_id, "桌上")
            )
            _ = await gateway.observe(ObservationRequest("o2", second.task_id, "桌上"))
            _ = gateway.finish_interaction(first.task_id)
            _ = evidence.get(result.frame.evidence_id)
            _ = gateway.finish_interaction(second.task_id)
            with self.assertRaises(KeyError):
                _ = evidence.get(result.frame.evidence_id)

    async def test_late_observation_after_cancel_is_released(self) -> None:
        """任务取消后才返回的观察被拒绝，其帧立即释放."""
        evidence = EvidenceStore()
        entered = asyncio.Event()
        allow = asyncio.Event()

        class GatedPerception(SimulatedPerception):
            """等待测试放行后才返回观察."""

            @override
            async def observe(self, request: ObservationRequest) -> ObservationResult:
                """先通知进入，再等待放行."""
                entered.set()
                _ = await allow.wait()
                return await super().observe(request)

        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(
                runtime, GatedPerception(evidence), _evidence=evidence
            )
            task = gateway.create_task("迟到观察")
            pending = asyncio.create_task(
                gateway.observe(ObservationRequest("o1", task.task_id, "桌上"))
            )
            _ = await entered.wait()
            _ = await gateway.cancel_task(task.task_id)
            allow.set()
            with self.assertRaises(ValueError):
                _ = await pending
            self.assertEqual(evidence.used_bytes, 0)

    async def test_terminal_listener_failure_does_not_block_terminal_state(
        self,
    ) -> None:
        """终态回调失败只记录日志，任务终态和其他回调照常."""
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            coordinator = TaskCoordinator(runtime)
            seen: list[int] = []

            def broken(_task_id: int) -> None:
                """模拟出错的回调."""
                raise RuntimeError("listener failed")

            coordinator.add_terminal_listener(broken)
            coordinator.add_terminal_listener(seen.append)
            task = coordinator.create_task("回调")
            with self.assertLogs("runtime.task_coordinator", level="ERROR"):
                state = coordinator.complete_task(
                    task.task_id, interaction_completed=True
                )
            self.assertEqual(state.status, TaskStatus.COMPLETED)
            self.assertEqual(seen, [task.task_id])


class CachedFrameTests(unittest.IsolatedAsyncioTestCase):
    """缓存帧证据被释放后不能再作为观察返回."""

    async def test_released_cache_frame_forces_new_capture(self) -> None:
        """账本里的帧已随任务结束释放时，重新采集而不是返回失效引用."""
        evidence = EvidenceStore()
        camera = FakeCamera(evidence)
        service = LivePerception(
            camera, FakeProvider(), SceneStore(), evidence, "cam-1", "qwen3.5:9b"
        )
        first = await service.observe(ObservationRequest("o1", 1, "桌上有什么"))
        evidence.release(first.frame.evidence_id)
        second = await service.observe(ObservationRequest("o2", 2, "桌上有什么"))
        self.assertFalse(second.from_cache)
        self.assertEqual(camera.captures, 2)
        _ = evidence.get(second.frame.evidence_id)
        await service.close()


class SpeechEvidenceTests(unittest.IsolatedAsyncioTestCase):
    """录音识别完、播报播完后释放音频证据."""

    async def test_recording_and_playback_audio_are_released(self) -> None:
        """一轮录音和播报结束后证据存储回到空."""
        evidence = EvidenceStore()
        device = AudioDeviceLease()
        async with ActionManager(SimulatedAdapter(), SkillRegistry()) as runtime:
            gateway = TeamGateway(runtime, SimulatedPerception(evidence))
            task = gateway.create_task("语音")
            speech_input = SpeechInputService(
                MockAudioRecorder(evidence, device),
                MockASRProvider(),
                gateway,
                _evidence=evidence,
            )
            speech_output = SpeechOutputService(
                MockTTSProvider(evidence),
                MockAudioPlayer(evidence, device),
                _evidence=evidence,
            )
            try:
                await speech_input.start(task.task_id, "recording", 15)
                self.assertIsNotNone(await speech_input.finish("recording"))
                self.assertEqual(evidence.used_bytes, 0)
                await speech_output.start("playback", "固定回答")
                state = await speech_output.wait_finished("playback")
                self.assertEqual(state.status, PlaybackStatus.COMPLETED)
                self.assertEqual(evidence.used_bytes, 0)
            finally:
                await speech_output.close()
                await speech_input.close()


if __name__ == "__main__":
    _ = unittest.main()
