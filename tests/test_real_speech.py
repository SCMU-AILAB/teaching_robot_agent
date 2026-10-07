# tests/test_real_speech.py
"""验证真实适配器的进程生命周期，普通测试不需要麦克风或模型下载."""

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import override
from unittest.mock import patch

from app.team_gateway import TeamGateway
from domain.services import AudioReference, PlaybackStatus
from perception.simulated import SimulatedPerception
from providers.asr.whisper import WhisperASRProvider
from providers.audio.real_player import PortAudioPlayer
from providers.audio.recorder import PortAudioRecorder
from providers.audio.wav import PcmAudio, encode_wav, save_wav
from providers.tts.system import SystemTTSProvider
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.registry import SkillRegistry
from speech.input import SpeechInputService
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


class RealSpeechTests(unittest.IsolatedAsyncioTestCase):
    """用真正的子进程固定时序，验证返回前已释放设备进程与租约."""

    def __init__(self, _method_name: str = "runTest") -> None:
        """配置每个用例独立资源，不在导入时启动进程."""
        super().__init__(_method_name)
        self.store: EvidenceStore = EvidenceStore()
        self.lease: AudioDeviceLease = AudioDeviceLease()
        self.directory: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path = Path()
        self.baseline: set[asyncio.Task[object]] = set()

    @override
    async def asyncSetUp(self) -> None:
        """建立工作目录，记录任务基线."""
        self.directory = tempfile.TemporaryDirectory(prefix="speech-test-")
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.baseline = set(asyncio.all_tasks())

    def command(self, scenario: str, name: str = "worker") -> tuple[str, ...]:
        """返回显式测试进程命令，绝不默认为真实设备."""
        return (
            sys.executable,
            "-m",
            "tests.speech_worker_fixture",
            scenario,
            str(self.root / name),
        )

    async def audio(self) -> AudioReference:
        """创建合法的已登记测试音频."""
        return await save_wav(
            self.store, encode_wav(PcmAudio(b"\0\0" * 1600, 16000, 1)), time.time()
        )

    async def wait_marker(self, name: str = "worker") -> None:
        """有界等待工作进程启动，不依赖任意长 sleep 固定竞态."""
        async with asyncio.timeout(3):
            while not (self.root / name).exists():
                await asyncio.sleep(0.005)

    def assert_released(self) -> None:
        """在兜底清理前检查所有实际子进程、后台任务和设备租约."""
        for marker in self.root.iterdir():
            if marker.is_file():
                pid = int(marker.read_text(encoding="ascii"))
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
        self.assertFalse(self.lease.busy)
        self.assertFalse(asyncio.all_tasks() - self.baseline - {asyncio.current_task()})

    def recorder(self, scenario: str = "normal") -> PortAudioRecorder:
        """构造注入设备工作进程替身的真实录音器."""
        recorder = PortAudioRecorder(
            self.store, self.lease, _worker_command=self.command(scenario)
        )
        self.addAsyncCleanup(recorder.close)
        return recorder

    def player(self, scenario: str = "normal") -> PortAudioPlayer:
        """构造真实播放器编排，进程只替换设备边界."""
        player = PortAudioPlayer(
            self.store, self.lease, _worker_command=self.command(scenario)
        )
        self.addAsyncCleanup(player.close)
        return player

    def asr(self, scenario: str = "normal") -> WhisperASRProvider:
        """替换推理进程，不绕过证据及响应校验."""
        return WhisperASRProvider(
            self.store, str(self.root), _worker_command=self.command(scenario, "asr")
        )

    async def test_record_three_rounds_and_finalize_once(self) -> None:
        """连续三次录音每次仅保存一次，并发 finish 和自动结束共享引用."""
        recorder = self.recorder()
        for index in range(3):
            key = str(index)
            await recorder.start(key, 0.02)
            first, second, third = await asyncio.gather(
                recorder.finish(key), recorder.wait_finished(key), recorder.finish(key)
            )
            self.assertEqual(first, second)
            self.assertEqual(first, third)
            self.assertEqual(recorder.finalize_count, index + 1)
            self.assertEqual(self.store.get(first.evidence_id).media_type, "audio/wav")
            self.assert_released()

    async def test_auto_finish_without_manual_request(self) -> None:
        """无需按钮即可按最长时长生成一次音频."""
        recorder = self.recorder()
        await recorder.start("one", 0.02)
        _ = await recorder.wait_finished("one")
        self.assertEqual(recorder.finalize_count, 1)
        self.assert_released()

    async def test_record_cancel_and_close_discard_audio(self) -> None:
        """取消及关闭都丢弃录音，不能产生迟到音频."""
        recorder = self.recorder("hang")
        await recorder.start("one", 10)
        await recorder.cancel("one")
        with self.assertRaises(asyncio.CancelledError):
            _ = await recorder.wait_finished("one")
        await recorder.start("two", 10)
        await recorder.close()
        self.assertEqual(recorder.finalize_count, 0)
        with self.assertRaises(RuntimeError):
            await recorder.start("three", 1)
        self.assert_released()

    async def test_no_device_and_empty_audio_are_failures(self) -> None:
        """启动失败与零帧录音均明确失败，不能生成音频引用."""
        recorder = self.recorder("fail")
        with self.assertRaises(RuntimeError):
            await recorder.start("missing", 1)
        self.assert_released()
        recorder = self.recorder("empty")
        await recorder.start("empty", 0.01)
        with self.assertRaises(ValueError):
            _ = await recorder.wait_finished("empty")
        self.assertEqual(recorder.finalize_count, 0)
        self.assert_released()

    async def test_external_record_wait_cancellation(self) -> None:
        """外部取消返回前，实际录音进程已经回收."""
        recorder = self.recorder("hang")
        await recorder.start("one", 10)
        waiter = asyncio.create_task(recorder.wait_finished("one"))
        await asyncio.sleep(0)
        _ = waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            _ = await waiter
        self.assert_released()

    async def test_player_complete_stop_and_exclusion(self) -> None:
        """完成与停止依实际进程反馈区分，播放期间录音拒绝占用."""
        audio = await self.audio()
        player = self.player()
        await player.play("complete", audio)
        self.assertEqual(
            (await player.wait_finished("complete")).status, PlaybackStatus.COMPLETED
        )
        player = self.player("hang")
        await player.play("stop", audio)
        recorder = self.recorder()
        with self.assertRaises(RuntimeError):
            await recorder.start("blocked", 1)
        first, second = await asyncio.gather(player.stop("stop"), player.stop("stop"))
        self.assertEqual(first, second)
        self.assertEqual(first.status, PlaybackStatus.STOPPED)
        self.assertEqual(player.play_count, 1)
        self.assert_released()

    async def test_player_stop_failure_is_not_success(self) -> None:
        """错误停止反馈不能标记 stopped，即使工作进程已经退出."""
        player = PortAudioPlayer(
            self.store, self.lease, _worker_command=self.command("bad-stop")
        )
        await player.play("one", await self.audio())
        with self.assertRaisesRegex(RuntimeError, "stop not confirmed"):
            _ = await player.stop("one")
        self.assertEqual((await player.get_state("one")).status, PlaybackStatus.FAILED)
        with self.assertRaises(RuntimeError):
            await player.close()
        self.assert_released()

    async def test_player_cancel_before_ready_requires_stop_confirmation(self) -> None:
        """设备启动但 READY 未到时，停止失败不能伪装为 stopped."""
        player = PortAudioPlayer(
            self.store, self.lease, _worker_command=self.command("early-bad-stop")
        )
        starting = asyncio.create_task(player.play("one", await self.audio()))
        await self.wait_marker("worker.device")
        with self.assertRaisesRegex(RuntimeError, "stop not confirmed"):
            _ = await player.stop("one")
        results = await asyncio.gather(starting, return_exceptions=True)
        self.assertIsInstance(results[0], RuntimeError)
        self.assertEqual((await player.get_state("one")).status, PlaybackStatus.FAILED)
        with self.assertRaises(RuntimeError):
            await player.close()
        self.assert_released()

    async def test_player_cancel_before_ready_accepts_confirmed_stop(self) -> None:
        """启动窗口收到真实 STOPPED 时可以正常结束并释放设备."""
        player = self.player("early-stop")
        starting = asyncio.create_task(player.play("one", await self.audio()))
        await self.wait_marker("worker.device")
        result = await player.stop("one")
        self.assertEqual(result.status, PlaybackStatus.STOPPED)
        _ = await asyncio.gather(starting, return_exceptions=True)
        self.assert_released()

    async def test_player_completed_feedback_survives_cancellation(self) -> None:
        """完成反馈已经送达但进程尚未退出时，取消仍保留 completed."""
        player = self.player("completed-before-exit")
        audio = await self.audio()
        await player.play("one", audio)
        await self.wait_marker("worker.device")
        result = await player.stop("one")
        self.assertEqual(result.status, PlaybackStatus.COMPLETED)
        # 成功完成不能关闭播放器；下一轮仍应允许启动。
        await player.play("two", audio)
        result = await player.stop("two")
        self.assertEqual(result.status, PlaybackStatus.COMPLETED)
        self.assert_released()

    async def test_player_external_cancellation_and_close(self) -> None:
        """外部等待取消和服务关闭都等待实际设备停止."""
        player = self.player("hang")
        audio = await self.audio()
        await player.play("one", audio)
        waiting = asyncio.create_task(player.wait_finished("one"))
        await asyncio.sleep(0)
        _ = waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            _ = await waiting
        self.assertEqual((await player.get_state("one")).status, PlaybackStatus.STOPPED)
        await player.play("two", audio)
        await player.close()
        self.assertEqual((await player.get_state("two")).status, PlaybackStatus.STOPPED)
        self.assert_released()

    async def test_asr_final_silence_invalid_and_failure(self) -> None:
        """最终文本、空语音和格式错误具有不同结果，不吞异常."""
        audio = await self.audio()
        self.assertEqual((await self.asr().transcribe(audio, 3)).text, "识别结果")
        self.assertEqual((await self.asr("empty").transcribe(audio, 3)).text, "")
        with self.assertRaises(ValueError):
            _ = await self.asr("malformed").transcribe(audio, 3)
        with self.assertRaises(RuntimeError):
            _ = await self.asr("fail").transcribe(audio, 3)
        self.assert_released()

    async def test_asr_timeout_and_cancel_reap_process(self) -> None:
        """超时与外部取消均终止本次真实推理进程，不留下后台工作."""
        audio = await self.audio()
        with self.assertRaises(TimeoutError):
            _ = await self.asr("hang").transcribe(audio, 0.3)
        self.assert_released()
        (self.root / "asr").unlink()
        waiting = asyncio.create_task(self.asr("hang").transcribe(audio, 10))
        await self.wait_marker("asr")
        _ = waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            _ = await waiting
        self.assert_released()

    async def test_input_cancel_during_asr_never_enqueues(self) -> None:
        """设备录音结束进入推理后取消，核心队列无迟到输入."""
        runtime = ActionManager(SimulatedAdapter(), SkillRegistry())
        perception = SimulatedPerception(self.store)
        gateway = TeamGateway(runtime, perception)
        service = SpeechInputService(self.recorder(), self.asr("hang"), gateway)
        self.addAsyncCleanup(service.close)
        self.addAsyncCleanup(perception.close)
        task_id = gateway.create_task("进程取消测试").task_id
        await service.start(task_id, "one", 0.01, 10)
        await self.wait_marker("asr")
        await service.cancel("one")
        self.assertEqual(service.get_state("one"), "cancelled")
        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(0.01):
                _ = await gateway.next_input()
        self.assert_released()

    async def test_tts_validates_evidence_and_cleans_temp_file(self) -> None:
        """系统 TTS 保存真实 WAV，文本通过标准输入且临时目录自动移除."""
        paths: list[Path] = []

        async def synthesize(
            command: tuple[str, ...], content: bytes, timeout_s: float
        ) -> bytes:
            """只替换系统命令，验证引擎参数及真实文件读取流程."""
            self.assertEqual(content.decode(), "测试文本")
            self.assertEqual(timeout_s, 3)
            path = Path(command[command.index("-o") + 1])
            paths.append(path)
            _ = path.write_bytes(encode_wav(PcmAudio(b"\0\0" * 1600, 16000, 1)))
            return b""

        with patch("providers.tts.system.run_process", new=synthesize):
            audio = await SystemTTSProvider(self.store, "say", "voice").synthesize(
                "测试文本", None, 3
            )
        self.assertEqual(audio.duration_s, 0.1)
        self.assertTrue(all(not path.exists() for path in paths))

    async def test_tts_failure_timeout_and_cancel_remove_temp_files(self) -> None:
        """任何合成退出路径均移除中间文件，取消不登记音频."""
        paths: list[Path] = []
        entered = asyncio.Event()

        async def hang(
            command: tuple[str, ...], _content: bytes, _timeout_s: float
        ) -> bytes:
            """在创建中间音频之后固定等待."""
            path = Path(command[command.index("-o") + 1])
            paths.append(path)
            _ = path.write_bytes(b"partial")
            entered.set()
            _ = await asyncio.Event().wait()
            return b""

        provider = SystemTTSProvider(self.store, "say", "voice")
        with patch("providers.tts.system.run_process", new=hang):
            with self.assertRaises(TimeoutError):
                _ = await provider.synthesize("测试", None, 0.02)
            entered.clear()
            waiting = asyncio.create_task(provider.synthesize("测试", None, 10))
            _ = await entered.wait()
            _ = waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                _ = await waiting
        with self.assertRaises(FileNotFoundError):
            _ = await SystemTTSProvider(
                self.store, "say", "voice", "/nonexistent/speech-engine"
            ).synthesize("测试", None, 3)
        self.assertTrue(all(not path.exists() for path in paths))
        self.assert_released()
