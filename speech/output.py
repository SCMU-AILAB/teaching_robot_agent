# speech/output.py
"""文本到模拟语音输出的内部编排，与 Runtime 和 HTTP 无关."""

import asyncio
import logging
from dataclasses import dataclass

from domain.services import PlaybackState, PlaybackStatus
from domain.validation import finite_float, nonempty_string
from speech._lifecycle import finish_cleanup
from speech.interfaces import AudioPlayer, TTSProvider

logger = logging.getLogger(__name__)


@dataclass
class _Output:
    """内部合成作业，播放状态始终由播放器提供."""

    task: asyncio.Task[PlaybackState]
    stage: str = "synthesizing"
    cancelled: bool = False
    attempted_play: bool = False
    cleanup: asyncio.Task[PlaybackState | None] | None = None


class SpeechOutputService:
    """拥有合成任务和播放器，不执行教学决策或创建机器人动作."""

    def __init__(self, _tts: TTSProvider, _player: AudioPlayer) -> None:
        """注入现有语音 Protocol，不启动后台任务."""
        self._tts: TTSProvider = _tts
        self._player: AudioPlayer = _player
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
        task = asyncio.create_task(self._run(playback_id, text, voice_id, timeout))
        self._outputs[playback_id] = _Output(task)
        task.add_done_callback(self._observe_failure)
        logger.info("[SpeechOutputService.start] 语音输出会话已启动")

    @staticmethod
    def _observe_failure(task: asyncio.Task[PlaybackState]) -> None:
        """观察后台异常，等待接口仍会传播原始失败."""
        if not task.cancelled():
            _ = task.exception()

    async def _stop_player(self, playback_id: str) -> PlaybackState | None:
        """合成尚未开始播放时没有播放终态，不伪造 stopped."""
        item = self._outputs[playback_id]
        if not item.attempted_play:
            return None
        try:
            return await self._player.stop(playback_id)
        except KeyError:
            # 开始播放可能在登记编号之前失败，没有可停止的播放资源。
            return None

    async def _run(
        self, playback_id: str, text: str, voice_id: str | None, timeout_s: float
    ) -> PlaybackState:
        """合成完成后播放，取消和失败都等待播放器停止."""
        item = self._outputs[playback_id]
        try:
            # ========== Step1: 合成只产生音频，不标记播放完成 ==========
            async with asyncio.timeout(timeout_s) as deadline:
                audio = await self._tts.synthesize(text, voice_id, timeout_s)
                if item.cancelled or self._closed:
                    raise asyncio.CancelledError
                if deadline.expired():
                    raise TimeoutError("Synthesis exceeded output deadline")
                # ========== Step2: 等待实际播放反馈再确认终态 ==========
                item.stage = "playing"
                item.attempted_play = True
                await self._player.play(playback_id, audio)
                if item.cancelled or self._closed:
                    raise asyncio.CancelledError
                state = await self._player.wait_finished(playback_id)
                if deadline.expired():
                    raise TimeoutError("Playback exceeded output deadline")
                if (
                    state.playback_id != playback_id
                    or state.status == PlaybackStatus.PLAYING
                ):
                    raise ValueError(
                        "Player did not return the requested terminal state"
                    )
                item.stage = state.status.value
                return state
        except asyncio.CancelledError:
            item.stage = "cancelled"
            raise
        except Exception:
            item.stage = "failed"
            raise
        finally:
            try:
                _ = await finish_cleanup(
                    asyncio.create_task(self._stop_player(playback_id))
                )
            except Exception:
                item.stage = "failed"
                raise

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
