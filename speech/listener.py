# speech/listener.py
"""免按键收音：持续采集，按语音检测切句，识别后作为语音输入提交；机器人说话或按键录音时让出麦克风."""

import asyncio
import logging
import re
import signal
import sys
import time
from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

from app.team_gateway import TeamGateway
from domain.services import AudioReference, PlaybackState, TranscriptResult
from providers.audio.wav import PcmAudio, encode_wav, save_wav
from speech.interfaces import ASRProvider, AudioPlayer, AudioRecorder
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore

logger = logging.getLogger(__name__)

_SUPERVISE_S = 0.2
# 去掉标点和空白后少于该字数的识别结果当作噪声
_MIN_CHARS = 2
_PUNCTUATION = re.compile(r"[\s，。！？、,.!?；;：:“”\"'…~～]+")
_ASR_TIMEOUT_S = 10.0


class PcmSource(Protocol):
    """流式采集：start 后反复 read 取得十六位单声道 PCM，stop 释放设备."""

    async def start(self) -> None:
        """打开设备，确认开始采集."""
        ...

    async def read(self) -> bytes:
        """取下一块 PCM；设备结束时抛出 EOFError."""
        ...

    async def stop(self) -> None:
        """停止采集并等待设备释放，重复调用安全."""
        ...


class VoiceDetector(Protocol):
    """语音检测：喂入 PCM，返回已经说完的句段."""

    @property
    def speaking(self) -> bool:
        """当前是否正处在一句话中间."""
        ...

    def accept(self, pcm: bytes) -> list[bytes]:
        """喂入一块 PCM，返回本次完成的句段（十六位 PCM）."""
        ...

    def reset(self) -> None:
        """丢弃未完成的句段."""
        ...


class WorkerPcmSource:
    """经音频工作进程 stream 模式采集；每块约 20 毫秒."""

    def __init__(
        self,
        _device: str = "",
        _sample_rate: int = 16000,
        _worker_command: tuple[str, ...] | None = None,
    ) -> None:
        """注入设备名称与采样率；构造时不启动进程."""
        self._device: str = _device
        self._rate: int = _sample_rate
        self._command: tuple[str, ...] = _worker_command or (
            sys.executable,
            "-m",
            "providers.audio.worker",
        )
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        """启动工作进程并等待 READY.

        Raises:
            RuntimeError: 麦克风打不开。
        """
        process = await asyncio.create_subprocess_exec(
            *self._command,
            "stream",
            "--device",
            self._device,
            "--rate",
            str(self._rate),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._process = process
        assert process.stdout is not None
        try:
            async with asyncio.timeout(10):
                if await process.stdout.readline() != b"READY\n":
                    raise RuntimeError("Microphone unavailable or failed to start")
        except BaseException:
            await self.stop()
            raise

    async def read(self) -> bytes:
        """读取下一块 PCM；进程退出时抛出 EOFError."""
        process = self._process
        if process is None or process.stdout is None:
            raise EOFError
        block = await process.stdout.read(self._rate // 50 * 2)
        if not block:
            raise EOFError
        return block

    async def stop(self) -> None:
        """发 SIGINT 让工作进程正常关闭设备；3 秒内不退出则强制结束."""
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        try:
            process.send_signal(signal.SIGINT)
        except ProcessLookupError:
            return
        try:
            async with asyncio.timeout(3):
                # 读完剩余输出，避免工作进程阻塞在写管道上无法退出
                if process.stdout is not None:
                    while await process.stdout.read(65536):
                        pass
                _ = await process.wait()
        except TimeoutError:
            process.kill()
            _ = await process.wait()


class HandsFreeListener:
    """监督循环每 0.2 秒对齐一次：开关打开、有进行中的任务且无人占用麦克风时采集."""

    def __init__(
        self,
        _source_factory: Callable[[], PcmSource],
        _detector: VoiceDetector,
        _asr: ASRProvider,
        _gateway: TeamGateway,
        _evidence: EvidenceStore,
        _lease: AudioDeviceLease,
        *,
        _enabled: Callable[[], bool],
        _active_task: Callable[[], int | None],
        _sample_rate: int = 16000,
        _wake_word: str = "",
        _resume_delay_s: float = 0.8,
    ) -> None:
        """注入采集、语音检测、识别、网关与设备租约，以及读取开关和当前任务的回调.

        Args:
            _source_factory: 每次开始采集时新建音源.
            _detector: 语音检测.
            _asr: 常驻识别.
            _gateway: 语音输入提交入口.
            _evidence: 句段音频的临时存储，识别后释放.
            _lease: 与录音器、播放器共用的设备租约.
            _enabled: 免按键对话开关.
            _active_task: 当前进行中的任务，没有时为 None.
            _sample_rate: 采样率.
            _wake_word: 非空时原话须以它开头，提交时去掉.
            _resume_delay_s: 机器人说完后等待回声消散、也让分句播报的下一句接上，再恢复采集，秒.
        """
        self._source_factory: Callable[[], PcmSource] = _source_factory
        self._detector: VoiceDetector = _detector
        self._asr: ASRProvider = _asr
        self._gateway: TeamGateway = _gateway
        self._evidence: EvidenceStore = _evidence
        self._lease: AudioDeviceLease = _lease
        self._enabled: Callable[[], bool] = _enabled
        self._active_task: Callable[[], int | None] = _active_task
        self._rate: int = _sample_rate
        self._wake_word: str = _wake_word.strip()
        self._resume_delay_s: float = _resume_delay_s
        self._holders: set[object] = set()
        self._resume_at: float = 0.0
        self._stream: asyncio.Task[None] | None = None
        self._stream_task_id: int | None = None
        self._segments: asyncio.Queue[tuple[int, bytes, float]] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._closed: bool = False
        self.submitted: int = 0

    @property
    def listening(self) -> bool:
        """正在采集."""
        return self._stream is not None and not self._stream.done()

    @property
    def hearing(self) -> bool:
        """正在采集且检测到有人说到一半."""
        return self.listening and self._detector.speaking

    def start(self) -> None:
        """启动监督循环与识别工作协程，重复调用安全."""
        if not self._workers:
            self._workers = [
                asyncio.create_task(self._supervise(), name="hands-free-supervisor"),
                asyncio.create_task(self._recognize(), name="hands-free-asr"),
            ]

    async def close(self) -> None:
        """停止采集与后台协程，释放麦克风."""
        self._closed = True
        await self._stop_stream()
        workers, self._workers = self._workers, []
        for worker in workers:
            _ = worker.cancel()
        _ = await asyncio.gather(*workers, return_exceptions=True)

    async def pause(self, owner: object) -> None:
        """播报或按键录音前调用：停止采集并释放麦克风，返回后设备租约已空出."""
        self._holders.add(owner)
        await self._stop_stream()

    def resume(self, owner: object) -> None:
        """对应 pause；所有占用方都结束、回声消散后由监督循环恢复采集."""
        self._holders.discard(owner)
        if not self._holders:
            self._resume_at = time.monotonic() + self._resume_delay_s

    async def _supervise(self) -> None:
        """对齐期望状态：该采集时开始，不该采集或任务变化时停止."""
        while not self._closed:
            task_id = self._active_task()
            wanted = (
                self._enabled()
                and task_id is not None
                and not self._holders
                and time.monotonic() >= self._resume_at
            )
            if self.listening and (not wanted or task_id != self._stream_task_id):
                await self._stop_stream()
            elif wanted and not self.listening and not self._lease.busy:
                assert task_id is not None
                await self._start_stream(task_id)
            await asyncio.sleep(_SUPERVISE_S)

    async def _start_stream(self, task_id: int) -> None:
        """取得设备租约并启动音源；麦克风打不开只记日志，下一轮重试."""
        owner = object()
        self._lease.acquire(owner)
        source = self._source_factory()
        try:
            await source.start()
        except Exception:
            self._lease.release(owner)
            logger.exception("[HandsFreeListener._start_stream] 麦克风打不开")
            await asyncio.sleep(2.0)
            return
        self._stream_task_id = task_id
        self._detector.reset()
        self._stream = asyncio.create_task(
            self._pump(source, task_id, owner), name="hands-free-stream"
        )

    async def _pump(self, source: PcmSource, task_id: int, owner: object) -> None:
        """读取 PCM 交给语音检测，完成的句段排队识别；结束时释放设备."""
        try:
            while True:
                block = await source.read()
                for segment in self._detector.accept(block):
                    self._segments.put_nowait((task_id, segment, time.time()))
        except EOFError:
            logger.warning("[HandsFreeListener._pump] 采集进程意外结束")
        finally:
            await source.stop()
            self._lease.release(owner)

    async def _stop_stream(self) -> None:
        """停止采集并等待设备释放；未说完的半句丢弃."""
        stream, self._stream = self._stream, None
        if stream is None:
            return
        # 取消采集协程，由它的收尾停止音源并释放设备租约
        _ = stream.cancel()
        _ = await asyncio.gather(stream, return_exceptions=True)
        self._detector.reset()

    async def _recognize(self) -> None:
        """逐句识别并提交；单句失败只记日志."""
        while True:
            task_id, segment, ended_at = await self._segments.get()
            try:
                await self._submit(task_id, segment, ended_at)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[HandsFreeListener._recognize] 句段识别失败")

    async def _submit(self, task_id: int, segment: bytes, ended_at: float) -> None:
        """识别一句；过滤噪声与唤醒词后提交到当前任务."""
        started_at = ended_at - len(segment) / (2 * self._rate)
        audio = await save_wav(
            self._evidence, encode_wav(PcmAudio(segment, self._rate, 1)), started_at
        )
        try:
            result = await self._asr.transcribe(audio, _ASR_TIMEOUT_S)
        finally:
            self._evidence.release(audio.evidence_id)
        text = accepted_text(result.text, self._wake_word)
        if text is None:
            logger.info("[HandsFreeListener._submit] 忽略 text=%r", result.text)
            return
        if not self._enabled() or self._active_task() != task_id:
            return
        _ = self._gateway.submit_transcript(
            task_id, f"hands-free-{uuid4().hex}", TranscriptResult(text)
        )
        self.submitted += 1
        logger.info(
            "[HandsFreeListener._submit] 提交 task_id=%s text=%s", task_id, text
        )


def accepted_text(text: str, wake_word: str = "") -> str | None:
    """去掉标点后少于 2 个字视为噪声；设了唤醒词时须以它开头并去掉它."""
    stripped = text.strip()
    if wake_word:
        bare = _PUNCTUATION.sub("", stripped)
        if not bare.startswith(wake_word):
            return None
        index = stripped.find(wake_word)
        stripped = stripped[index + len(wake_word) :].lstrip("，,。. 、")
    if len(_PUNCTUATION.sub("", stripped)) < _MIN_CHARS:
        return None
    return stripped


class MicrophoneGate(Protocol):
    """占用麦克风前让免按键收音停下，用完后恢复."""

    async def pause(self, owner: object) -> None:
        """停止收音并释放设备."""
        ...

    def resume(self, owner: object) -> None:
        """对应 pause."""
        ...


class GatedRecorder:
    """按键录音期间暂停免按键收音."""

    def __init__(self, _inner: AudioRecorder, _gate: MicrophoneGate) -> None:
        """包装录音器."""
        self._inner: AudioRecorder = _inner
        self._gate: MicrophoneGate = _gate

    async def start(self, recording_id: str, max_duration_s: float) -> None:
        """先让收音停下再开始录音；启动失败立即恢复."""
        await self._gate.pause(("record", recording_id))
        try:
            await self._inner.start(recording_id, max_duration_s)
        except BaseException:
            self._gate.resume(("record", recording_id))
            raise

    async def finish(self, recording_id: str) -> AudioReference:
        """结束录音后恢复收音."""
        try:
            return await self._inner.finish(recording_id)
        finally:
            self._gate.resume(("record", recording_id))

    async def wait_finished(self, recording_id: str) -> AudioReference:
        """录音到时自动结束后恢复收音."""
        try:
            return await self._inner.wait_finished(recording_id)
        finally:
            self._gate.resume(("record", recording_id))

    async def cancel(self, recording_id: str) -> None:
        """放弃录音后恢复收音."""
        try:
            await self._inner.cancel(recording_id)
        finally:
            self._gate.resume(("record", recording_id))

    async def close(self) -> None:
        """关闭录音器."""
        await self._inner.close()


class GatedPlayer:
    """播放期间暂停免按键收音，避免录到机器人自己的声音."""

    def __init__(self, _inner: AudioPlayer, _gate: MicrophoneGate) -> None:
        """包装播放器."""
        self._inner: AudioPlayer = _inner
        self._gate: MicrophoneGate = _gate

    async def play(self, playback_id: str, audio: AudioReference) -> None:
        """先让收音停下再播放；启动失败立即恢复."""
        await self._gate.pause(("play", playback_id))
        try:
            await self._inner.play(playback_id, audio)
        except BaseException:
            self._gate.resume(("play", playback_id))
            raise

    async def wait_finished(self, playback_id: str) -> PlaybackState:
        """播完后恢复收音."""
        try:
            return await self._inner.wait_finished(playback_id)
        finally:
            self._gate.resume(("play", playback_id))

    async def stop(self, playback_id: str) -> PlaybackState:
        """停止播放后恢复收音."""
        try:
            return await self._inner.stop(playback_id)
        finally:
            self._gate.resume(("play", playback_id))

    async def get_state(self, playback_id: str) -> PlaybackState:
        """读取播放状态."""
        return await self._inner.get_state(playback_id)

    async def close(self) -> None:
        """关闭播放器."""
        await self._inner.close()
