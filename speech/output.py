# speech/output.py
"""文本到模拟语音输出的内部编排，与 Runtime 和 HTTP 无关."""

import asyncio
import logging
import re
from dataclasses import dataclass, field, replace

from domain.services import AudioReference, PlaybackState, PlaybackStatus
from domain.validation import finite_float, nonempty_string
from speech._lifecycle import finish_cleanup
from speech.interfaces import AudioPlayer, TTSProvider
from storage.evidence import EvidenceStore

logger = logging.getLogger(__name__)

# 分句：在句末标点后切开；短于下限的句子并入下一句，避免过碎的合成请求。
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])")
_MIN_SEGMENT_CHARS = 12
# 不超过该长度的文本整段合成，保持单段播放。
_PIPELINE_MIN_CHARS = 30
# 首段在第一个逗号或句末处提前切出，先开口再合成其余部分。GPT-SoVITS 每段约 0.6 秒固定开销、
# 每字约 0.035 秒，播放约为合成的 2–3 倍（2026-10-31 实测）；首段过短会播完时下一段还没合成好。
_CLAUSE_END = re.compile(r"[，,。！？!?；;]")
_FIRST_SEGMENT_MIN_CHARS = 8
_FIRST_SPLIT_MIN_CHARS = 16


def split_for_speech(text: str) -> list[str]:
    """把朗读文本切成依次合成播放的句段：首段尽早在逗号或句末切出，其余按句切."""
    if len(text) >= _FIRST_SPLIT_MIN_CHARS:
        for match in _CLAUSE_END.finditer(text):
            end = match.end()
            if end < _FIRST_SEGMENT_MIN_CHARS:
                continue
            rest = text[end:]
            if len(rest.strip()) >= _FIRST_SEGMENT_MIN_CHARS:
                return [text[:end], *_split_sentences(rest)]
            break
    return _split_sentences(text)


def _split_sentences(text: str) -> list[str]:
    """按句末标点切段；短文本或没有句末标点时整段返回."""
    if len(text) <= _PIPELINE_MIN_CHARS:
        return [text]
    segments: list[str] = []
    buffer = ""
    for sentence in _SENTENCE_END.split(text):
        buffer += sentence
        if len(buffer) >= _MIN_SEGMENT_CHARS:
            segments.append(buffer)
            buffer = ""
    if buffer.strip():
        if segments:
            segments[-1] += buffer
        else:
            segments.append(buffer)
    return segments


@dataclass
class _Output:
    """内部合成作业，播放状态始终由播放器提供."""

    task: asyncio.Task[PlaybackState]
    stage: str = "synthesizing"
    cancelled: bool = False
    attempted_play: bool = False
    play_started: bool = False
    cleanup: asyncio.Task[PlaybackState | None] | None = None
    # 分句播放时当前句段在播放器中的编号；首句沿用输出编号。
    current: str = ""
    # 全部句段结束后汇总的终态，供验证读取。
    final: PlaybackState | None = None
    # 已登记但尚未释放的合成音频。
    audio: list[AudioReference] = field(default_factory=list)


class SpeechOutputService:
    """拥有合成任务和播放器，不执行教学决策或创建机器人动作."""

    def __init__(
        self,
        _tts: TTSProvider,
        _player: AudioPlayer,
        *,
        _evidence: EvidenceStore | None = None,
    ) -> None:
        """注入现有语音 Protocol，不启动后台任务.

        Args:
            _tts: 语音合成适配器.
            _player: 播放器.
            _evidence: 合成音频所在的证据存储；提供时播放器确认停止后释放该音频.
        """
        self._tts: TTSProvider = _tts
        self._player: AudioPlayer = _player
        self._evidence: EvidenceStore | None = _evidence
        self._outputs: dict[str, _Output] = {}
        self._closed: bool = False
        self._close_task: asyncio.Task[None] | None = None

    async def start(
        self,
        playback_id: str,
        text: str,
        voice_id: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        """开始一次输出，合成与播放共享执行期限.

        Args:
            playback_id: 调用方分配的唯一播放编号。
            text: 要合成的非空文本。
            voice_id: 可选音色标识。
            timeout_s: 合成和播放共用期限，单位为秒。

        Raises:
            ValueError: 参数非法或编号重复。
            RuntimeError: 服务已关闭或已有活动输出。
        """
        playback_id = nonempty_string(playback_id, "playback_id")
        text = nonempty_string(text, "text")
        if voice_id is not None:
            voice_id = nonempty_string(voice_id, "voice_id")
        timeout = finite_float(timeout_s, "timeout_s")
        if timeout <= 0:
            raise ValueError("timeout_s must be positive")
        if self._closed:
            raise RuntimeError("Speech output closed")
        if playback_id in self._outputs:
            raise ValueError("Output already exists")
        if any(not item.task.done() for item in self._outputs.values()):
            raise RuntimeError("Speech output busy")
        if len(self._outputs) >= 4096:
            self._prune_finished()
        if len(self._outputs) >= 4096:
            raise RuntimeError("Output retention capacity reached")
        task = asyncio.create_task(self._run(playback_id, text, voice_id, timeout))
        self._outputs[playback_id] = _Output(task, current=playback_id)
        task.add_done_callback(self._observe_failure)
        logger.info("[SpeechOutputService.start] 语音输出会话已启动")

    def _prune_finished(self) -> None:
        """达到保留上限时清理作业与停止清理都已结束的旧输出."""
        for key in [
            key
            for key, item in self._outputs.items()
            if item.task.done() and (item.cleanup is None or item.cleanup.done())
        ]:
            del self._outputs[key]

    @staticmethod
    def _observe_failure(task: asyncio.Task[PlaybackState]) -> None:
        """观察后台异常，等待接口仍会传播原始失败."""
        if not task.cancelled():
            _ = task.exception()

    async def _stop_player(self, playback_id: str) -> PlaybackState | None:
        """合成尚未开始播放时没有播放终态，不伪造 stopped；分句时停止当前句段."""
        item = self._outputs[playback_id]
        if not item.attempted_play:
            return None
        try:
            state = await self._player.stop(item.current)
            self._validate_terminal(state, item.current)
            return replace(state, playback_id=playback_id)
        except KeyError:
            if item.play_started:
                raise RuntimeError(
                    "Started playback lost its stop confirmation"
                ) from None
            # 开始播放可能在登记编号之前失败，没有可停止的播放资源。
            return None

    @staticmethod
    def _validate_terminal(state: PlaybackState, playback_id: str) -> None:
        """拒绝错配编号、非终态或缺失结束时间的播放器反馈."""
        if (
            state.playback_id != playback_id
            or state.status
            not in {
                PlaybackStatus.COMPLETED,
                PlaybackStatus.STOPPED,
                PlaybackStatus.FAILED,
            }
            or state.ended_at is None
        ):
            raise RuntimeError("Playback termination is not confirmed")
        started = finite_float(state.started_at, "started_at")
        ended = finite_float(state.ended_at, "ended_at")
        if started < 0 or ended < started:
            raise ValueError("Invalid playback timestamps")

    async def get_playback_state(self, playback_id: str) -> PlaybackState:
        """查询已登记输出的真实播放状态；分句全部结束后返回汇总终态."""
        item = self._outputs[playback_id]
        if item.final is not None:
            return item.final
        state = await self._player.get_state(item.current)
        self._validate_terminal(state, item.current)
        return replace(state, playback_id=playback_id)

    async def _run(
        self, playback_id: str, text: str, voice_id: str | None, timeout_s: float
    ) -> PlaybackState:
        """按句段流水线合成与播放：播放当前句时合成下一句；取消和失败都等待播放器停止."""
        item = self._outputs[playback_id]
        segments = split_for_speech(text)
        pending: asyncio.Task[AudioReference] | None = None
        try:
            async with asyncio.timeout(timeout_s) as deadline:
                synthesis = asyncio.create_task(
                    self._tts.synthesize(segments[0], voice_id, timeout_s)
                )
                pending = synthesis
                states: list[PlaybackState] = []
                for index in range(len(segments)):
                    # ========== Step1: 取得本句音频并预取下一句 ==========
                    audio = await synthesis
                    pending = None
                    item.audio.append(audio)
                    if item.cancelled or self._closed:
                        raise asyncio.CancelledError
                    if deadline.expired():
                        raise TimeoutError("Synthesis exceeded output deadline")
                    if index + 1 < len(segments):
                        synthesis = asyncio.create_task(
                            self._tts.synthesize(
                                segments[index + 1], voice_id, timeout_s
                            )
                        )
                        pending = synthesis
                    # ========== Step2: 播放本句并等待实际播放反馈 ==========
                    segment_id = playback_id if index == 0 else f"{playback_id}#{index}"
                    item.current = segment_id
                    item.stage = "playing"
                    item.attempted_play = True
                    item.play_started = False
                    await self._player.play(segment_id, audio)
                    item.play_started = True
                    if item.cancelled or self._closed:
                        raise asyncio.CancelledError
                    state = await self._player.wait_finished(segment_id)
                    if deadline.expired():
                        raise TimeoutError("Playback exceeded output deadline")
                    if (
                        state.playback_id != segment_id
                        or state.status == PlaybackStatus.PLAYING
                    ):
                        raise ValueError(
                            "Player did not return the requested terminal state"
                        )
                    self._validate_terminal(state, segment_id)
                    states.append(state)
                    # 本句播放已结束，音频不再被读取。
                    self._release_audio(item, audio)
                    if state.status != PlaybackStatus.COMPLETED:
                        break
                final = replace(
                    states[-1],
                    playback_id=playback_id,
                    started_at=states[0].started_at,
                )
                item.final = final
                item.stage = final.status.value
                return final
        except asyncio.CancelledError:
            item.stage = "cancelled"
            raise
        except Exception:
            item.stage = "failed"
            raise
        finally:
            # 预取中的下一句合成随之取消；迟到的音频与已登记音频一并在停止确认后释放。
            if pending is not None:
                _ = pending.cancel()
                results = await asyncio.gather(pending, return_exceptions=True)
                if isinstance(results[0], AudioReference):
                    item.audio.append(results[0])
            try:
                _ = await finish_cleanup(
                    asyncio.create_task(self._stop_player(playback_id))
                )
            except Exception:
                item.stage = "failed"
                raise
            # 播放器确认停止后音频不再被读取；停止未确认时保留证据供排查.
            for audio in tuple(item.audio):
                self._release_audio(item, audio)

    def _release_audio(self, item: _Output, audio: AudioReference) -> None:
        """释放一段合成音频并从待释放列表移除."""
        if audio in item.audio:
            item.audio.remove(audio)
        if self._evidence is not None:
            self._evidence.release(audio.evidence_id)

    def get_stage(self, playback_id: str) -> str:
        """读取内部编排阶段，不能用它代替 PlaybackState."""
        return self._outputs[playback_id].stage

    async def wait_finished(self, playback_id: str) -> PlaybackState:
        """等待输出，取消外部等待时也清理合成和播放."""
        try:
            return await asyncio.shield(self._outputs[playback_id].task)
        except asyncio.CancelledError:
            _ = await self.stop(playback_id)
            raise

    def _begin_stop(self, playback_id: str) -> asyncio.Task[PlaybackState | None]:
        """取消标志先于协程取消设置，阻止迟到合成自动播放."""
        item = self._outputs[playback_id]
        if item.cleanup is None:
            item.cancelled = True
            if not item.task.done():
                item.stage = "cancelled"
                _ = item.task.cancel()
            item.cleanup = asyncio.create_task(self._stop(playback_id))
        return item.cleanup

    async def _stop(self, playback_id: str) -> PlaybackState | None:
        """等待输出退出并取得播放器确认的终态."""
        item = self._outputs[playback_id]
        _ = await asyncio.gather(item.task, return_exceptions=True)
        return await self._stop_player(playback_id)

    async def stop(self, playback_id: str) -> PlaybackState | None:
        """停止输出；合成期间返回 None，播放期间返回实际终态."""
        return await finish_cleanup(self._begin_stop(playback_id))

    async def close(self) -> None:
        """关闭并清理所有输出，调用方取消不会中断释放."""
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await finish_cleanup(self._close_task)

    async def _close(self) -> None:
        """先取消所有作业，再关闭拥有的播放器."""
        try:
            tasks = [self._begin_stop(key) for key in self._outputs]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        finally:
            await self._player.close()
            logger.info("[SpeechOutputService._close] 语音输出资源已释放")
