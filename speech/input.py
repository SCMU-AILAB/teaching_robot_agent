# speech/input.py
"""录音到核心输入的内部编排，不提供 HTTP 或教学决策."""

import asyncio
import logging
from dataclasses import dataclass

from app.team_gateway import TeamGateway, UserInput
from domain.services import TranscriptResult
from domain.validation import finite_float, nonempty_string, positive_int
from speech._lifecycle import finish_cleanup
from speech.interfaces import ASRProvider, AudioRecorder

logger = logging.getLogger(__name__)


@dataclass
class _Session:
    """每次录音仅有一个识别作业，状态只供内部诊断."""

    task: asyncio.Task[UserInput | None]
    task_id: int
    state: str = "recording"
    cancelled: bool = False
    cleanup_failed: bool = False
    cleanup: asyncio.Task[None] | None = None


class SpeechInputService:
    """拥有注入的录音器及识别作业，转写去重交给核心门面."""

    def __init__(
        self, _recorder: AudioRecorder, _asr: ASRProvider, _gateway: TeamGateway
    ) -> None:
        """注入已有接口，不在构造期间启动任务."""
        self._recorder: AudioRecorder = _recorder
        self._asr: ASRProvider = _asr
        self._gateway: TeamGateway = _gateway
        self._sessions: dict[str, _Session] = {}
        self._closed: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._starting: set[int] = set()
        self._startup_failures: set[int] = set()

    async def start(
        self,
        task_id: int,
        recording_id: str,
        max_duration_s: float,
        timeout_s: float = 30.0,
    ) -> None:
        """开始一次录音及唯一识别作业，拒绝重用会话编号.

        Args:
            task_id: 现有核心任务编号。
            recording_id: 调用方提供的本地录音编号。
            max_duration_s: 自动结束录音的最长等待秒数。
            timeout_s: 录音结束后识别的期限，单位为秒。

        Raises:
            ValueError: 参数非法或录音编号重复。
            RuntimeError: 服务关闭或录音设备被占用。
        """
        task_id = positive_int(task_id, "task_id")
        recording_id = nonempty_string(recording_id, "recording_id")
        duration = finite_float(max_duration_s, "max_duration_s")
        timeout = finite_float(timeout_s, "timeout_s")
        if duration <= 0 or timeout <= 0:
            raise ValueError("Durations must be positive")
        async with self._lock:
            if self._closed:
                raise RuntimeError("Speech input closed")
            if recording_id in self._sessions:
                raise ValueError("Session already exists")
            self._gateway.require_active(task_id)
            if len(self._sessions) >= 4096:
                raise RuntimeError("Recording retention capacity reached")
            self._starting.add(task_id)
            try:
                await self._recorder.start(recording_id, duration)
                try:
                    self._gateway.require_active(task_id)
                except ValueError:
                    try:
                        await finish_cleanup(
                            asyncio.create_task(self._recorder.cancel(recording_id))
                        )
                    except BaseException:
                        self._startup_failures.add(task_id)
                        raise
                    raise
            except asyncio.CancelledError:
                self._closed = True
                try:
                    await finish_cleanup(asyncio.create_task(self._recorder.close()))
                except BaseException:
                    self._startup_failures.add(task_id)
                    raise
                raise
            finally:
                self._starting.discard(task_id)
            task = asyncio.create_task(self._run(task_id, recording_id, timeout))
            self._sessions[recording_id] = _Session(task, task_id)
            task.add_done_callback(self._observe_failure)
            logger.info("[SpeechInputService.start] 语音输入会话已启动")

    @staticmethod
    def _validate_fields(text: object, is_final: object) -> None:
        """检查转写字段的实际类型，拒绝错误适配器结果."""
        if not isinstance(text, str) or not isinstance(is_final, bool):
            raise ValueError("Invalid transcript fields")

    @staticmethod
    def _validate_transcript(value: object) -> TranscriptResult:
        """校验适配器输入边界后再收窄具体类型."""
        if not isinstance(value, TranscriptResult):
            raise ValueError("Invalid transcript")
        SpeechInputService._validate_fields(value.text, value.is_final)
        return value

    @staticmethod
    def _observe_failure(task: asyncio.Task[UserInput | None]) -> None:
        """读取后台异常避免未等待告警，结果仍通过等待方法重新抛出."""
        if not task.cancelled():
            _ = task.exception()

    async def _run(
        self, task_id: int, recording_id: str, timeout_s: float
    ) -> UserInput | None:
        """单次录音完成后识别；失败和取消先清理再传播."""
        session = self._sessions[recording_id]
        try:
            # ========== Step1: 等待录音完成，获取已登记证据 ==========
            audio = await self._recorder.wait_finished(recording_id)
            if session.cancelled or self._closed:
                raise asyncio.CancelledError
            session.state = "finished"
            # ========== Step2: 识别并实际校验外部返回值 ==========
            session.state = "transcribing"
            async with asyncio.timeout(timeout_s) as deadline:
                transcript = self._validate_transcript(
                    await self._asr.transcribe(audio, timeout_s)
                )
            if session.cancelled or self._closed:
                raise asyncio.CancelledError
            if deadline.expired():
                raise TimeoutError("Recognition exceeded input deadline")
            # ========== Step3: 核心负责最终任务校验、去重和编号 ==========
            item = None
            if transcript.is_final and transcript.text.strip():
                item = self._gateway.submit_transcript(
                    task_id, recording_id, transcript
                )
            session.state = "completed"
            return item
        except asyncio.CancelledError:
            session.state = "cancelled"
            raise
        except Exception:
            session.state = "failed"
            raise
        finally:
            try:
                await finish_cleanup(
                    asyncio.create_task(self._recorder.cancel(recording_id))
                )
            except BaseException:
                session.cleanup_failed = True
                raise

    def is_busy(self, task_id: int) -> bool:
        """录音启动、采集、识别或清理未完成时阻止任务验收."""
        return (
            task_id in self._startup_failures
            or task_id in self._starting
            or any(
                session.task_id == task_id
                and (
                    session.cleanup_failed
                    or not session.task.done()
                    or (session.cleanup is not None and not session.cleanup.done())
                )
                for session in self._sessions.values()
            )
        )

    async def cancel_task(self, task_id: int) -> None:
        """核心先阻止新输入，再等待此任务在途启动及已有会话退出."""
        async with self._lock:
            tasks = [
                self._begin_cancel(key)
                for key, session in self._sessions.items()
                if session.task_id == task_id
            ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        if task_id in self._startup_failures:
            raise RuntimeError("Recording startup cleanup could not be confirmed")

    def get_state(self, recording_id: str) -> str:
        """读取内部会话状态，不作为公共 API 状态合同."""
        return self._sessions[recording_id].state

    async def wait_finished(self, recording_id: str) -> UserInput | None:
        """等待输入结果；外部等待取消时取消并清理整个会话."""
        try:
            return await asyncio.shield(self._sessions[recording_id].task)
        except asyncio.CancelledError:
            await self.cancel(recording_id)
            raise

    async def finish(self, recording_id: str) -> UserInput | None:
        """主动结束录音并等待唯一识别作业返回结果."""
        try:
            _ = await self._recorder.finish(recording_id)
            return await self.wait_finished(recording_id)
        except asyncio.CancelledError:
            await self.cancel(recording_id)
            raise

    async def cancel(self, recording_id: str) -> None:
        """取消识别和录音并等待退出，不取消核心任务."""
        await finish_cleanup(self._begin_cancel(recording_id))

    def _begin_cancel(self, recording_id: str) -> asyncio.Task[None]:
        """每个会话只发送一次取消，避免重复打断其清理."""
        session = self._sessions[recording_id]
        if session.cleanup is None:
            session.cancelled = True
            if not session.task.done():
                session.state = "cancelled"
                _ = session.task.cancel()
            session.cleanup = asyncio.create_task(self._cancel_session(recording_id))
        return session.cleanup

    async def _cancel_session(self, recording_id: str) -> None:
        """等待会话退出，并清理尚未开始执行的录音任务."""
        session = self._sessions[recording_id]
        _ = await asyncio.gather(session.task, return_exceptions=True)
        await self._recorder.cancel(recording_id)

    async def close(self) -> None:
        """禁止新会话，等待全部会话和录音器释放，多次关闭安全."""
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await finish_cleanup(self._close_task)

    async def _close(self) -> None:
        """一次性取消所有会话，避免逐个等待期间其他会话继续提交."""
        async with self._lock:
            try:
                tasks = [self._begin_cancel(key) for key in self._sessions]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
            finally:
                await self._recorder.close()
                logger.info("[SpeechInputService._close] 语音输入资源已释放")
