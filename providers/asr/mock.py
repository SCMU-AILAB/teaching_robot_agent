# providers/asr/mock.py
"""可注入文本、异常和延迟的本地识别模拟器."""

import asyncio

from domain.services import AudioReference, TranscriptResult
from domain.validation import finite_float, nonempty_string


class MockASRProvider:
    """仅返回测试注入的结果，不连接网络或声学模型."""

    def __init__(
        self,
        _result: TranscriptResult | None = None,
        _error: Exception | None = None,
        _delay_s: float = 0.0,
        _release: asyncio.Event | None = None,
    ) -> None:
        """配置确定性结果和用于竞态测试的释放信号."""
        self._result: TranscriptResult = (
            _result if _result is not None else TranscriptResult("模拟识别文本")
        )
        self._error: Exception | None = _error
        self._delay_s: float = finite_float(_delay_s, "delay_s")
        if self._delay_s < 0:
            raise ValueError("delay_s must be nonnegative")
        self._release: asyncio.Event | None = _release
        self.entered: asyncio.Event = asyncio.Event()
        self.call_count: int = 0

    async def transcribe(
        self, audio: AudioReference, timeout_s: float
    ) -> TranscriptResult:
        """在期限内返回结果；异常和取消继续向调用方传播."""
        timeout = finite_float(timeout_s, "timeout_s")
        _ = nonempty_string(audio.audio_id, "audio_id")
        if timeout <= 0:
            raise ValueError("timeout_s must be positive")
        self.call_count += 1
        self.entered.set()
        async with asyncio.timeout(timeout):
            if self._release is not None:
                _ = await self._release.wait()
            await asyncio.sleep(self._delay_s)
            if self._error is not None:
                raise self._error
            return self._result
