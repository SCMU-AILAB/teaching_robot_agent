# providers/asr/resident.py
"""常驻进程内的离线识别：应用启动时加载一次模型，关闭时释放."""

import asyncio
import logging
from typing import Protocol

from domain.services import AudioReference, TranscriptResult
from domain.validation import finite_float
from providers.audio.wav import read_wav
from speech._lifecycle import finish_cleanup
from storage.evidence import EvidenceStore

logger = logging.getLogger(__name__)


class ASREngine(Protocol):
    """具体识别引擎；load、recognize、unload 均可能阻塞，由调用方放入线程."""

    @property
    def name(self) -> str:
        """日志使用的引擎名称."""
        ...

    def check_files(self) -> None:
        """确认本地模型文件齐全，缺失时抛出 ValueError，不自动下载."""
        ...

    def load(self) -> None:
        """加载模型到内存."""
        ...

    def recognize(self, content: bytes) -> tuple[str, str]:
        """识别已校验的 PCM WAV，返回最终文本与语言."""
        ...

    def unload(self) -> None:
        """释放模型."""
        ...


class ResidentASRProvider:
    """模型生命周期由应用管理；同一模型串行推理，取消须等推理真正结束."""

    def __init__(self, _evidence: EvidenceStore, _engine: ASREngine) -> None:
        """只保存依赖，构造期间不加载模型或导入识别引擎.

        Args:
            _evidence: 录音所在的证据存储.
            _engine: 具体识别引擎.
        """
        self._evidence: EvidenceStore = _evidence
        self._engine: ASREngine = _engine
        self._loaded: bool = False
        self._closed: bool = False
        # 一把锁串行加载、推理和释放，保证模型不会在推理中被释放。
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def engine(self) -> ASREngine:
        """当前装配的识别引擎，供装配检查."""
        return self._engine

    async def start(self) -> None:
        """加载一次模型，重复调用安全；缺少本地模型时明确失败.

        Raises:
            ValueError: 本地模型文件缺失.
            RuntimeError: 识别器已关闭.
        """
        async with self._lock:
            if self._closed:
                raise RuntimeError("ASR provider is closed")
            if self._loaded:
                return
            self._engine.check_files()
            # 加载在线程中执行；调用方取消时等待加载结束再传播，并释放迟到模型。
            load = asyncio.create_task(asyncio.to_thread(self._engine.load))
            try:
                await finish_cleanup(load)
            except asyncio.CancelledError:
                if not load.cancelled() and load.exception() is None:
                    await asyncio.to_thread(self._engine.unload)
                raise
            self._loaded = True
            logger.info(
                "[ResidentASRProvider.start] 识别模型已加载 engine=%s",
                self._engine.name,
            )

    async def transcribe(
        self, audio: AudioReference, timeout_s: float
    ) -> TranscriptResult:
        """识别已登记音频；无语音返回空文本.

        Args:
            audio: 已登记的 PCM WAV 引用.
            timeout_s: 含排队等待的识别期限，秒.

        Returns:
            最终转写结果.

        Raises:
            ValueError: 期限或音频非法.
            RuntimeError: 模型未加载或已关闭.
            TimeoutError: 超过期限；推理结束后才抛出.
            asyncio.CancelledError: 调用方取消；推理结束后才传播.
        """
        timeout = finite_float(timeout_s, "timeout_s")
        if timeout <= 0:
            raise ValueError("ASR timeout must be positive")
        self._require_loaded()
        content = await read_wav(self._evidence, audio)
        async with asyncio.timeout(timeout), self._lock:
            # 等锁期间可能已经关闭，进入临界区后重新确认模型仍可用。
            self._require_loaded()
            # 原生推理无法强行中断：取消或超时先等线程结束，避免与释放并发。
            text, language = await finish_cleanup(
                asyncio.create_task(asyncio.to_thread(self._engine.recognize, content))
            )
        return TranscriptResult(text, language, True)

    def _require_loaded(self) -> None:
        """未加载或已关闭时明确失败，不临时加载模型."""
        if not self._loaded or self._closed:
            raise RuntimeError("ASR model is not loaded")

    async def close(self) -> None:
        """拒绝新请求，等待进行中的推理结束后释放模型，重复调用安全."""
        self._closed = True
        async with self._lock:
            if self._loaded:
                self._loaded = False
                await asyncio.to_thread(self._engine.unload)
                logger.info(
                    "[ResidentASRProvider.close] 识别模型已释放 engine=%s",
                    self._engine.name,
                )
