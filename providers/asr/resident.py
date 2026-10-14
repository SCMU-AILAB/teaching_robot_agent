# providers/asr/resident.py
"""常驻进程内的离线识别：应用启动时加载一次模型，关闭时释放."""

import asyncio
import logging
from pathlib import Path

from domain.services import AudioReference, TranscriptResult
from domain.validation import finite_float, nonempty_string
from providers.asr.whisper_engine import (
    WhisperModel,
    WhisperModelFactory,
    load_faster_whisper,
    transcribe_wav,
)
from providers.audio.wav import read_wav
from speech._lifecycle import finish_cleanup
from storage.evidence import EvidenceStore

logger = logging.getLogger(__name__)


class ResidentWhisperASRProvider:
    """模型生命周期由应用管理；同一模型串行推理，取消须等推理真正结束."""

    def __init__(
        self,
        _evidence: EvidenceStore,
        _model_path: str,
        _language: str = "zh",
        _device: str = "cpu",
        _compute_type: str = "int8",
        *,
        _model_factory: WhisperModelFactory | None = None,
    ) -> None:
        """只保存配置，构造期间不加载模型或导入识别引擎.

        Args:
            _evidence: 录音所在的证据存储.
            _model_path: 已下载的本地模型目录，须包含 model.bin.
            _language: 识别语言代码.
            _device: cpu 或 cuda.
            _compute_type: 引擎计算精度，例如 int8 或 float16.
            _model_factory: 测试注入的模型构造函数；缺省在启动时导入 faster-whisper.

        Raises:
            ValueError: 设备或文本参数非法.
        """
        if _device not in {"cpu", "cuda"}:
            raise ValueError("ASR compute device must be cpu or cuda")
        self._evidence: EvidenceStore = _evidence
        self._model_path: str = nonempty_string(_model_path, "model_path")
        self._language: str = nonempty_string(_language, "language")
        self._device: str = _device
        self._compute_type: str = nonempty_string(_compute_type, "compute_type")
        self._factory: WhisperModelFactory | None = _model_factory
        self._model: WhisperModel | None = None
        self._closed: bool = False
        # 一把锁串行加载、推理和释放，保证模型不会在推理中被释放。
        self._lock: asyncio.Lock = asyncio.Lock()

    async def start(self) -> None:
        """加载一次模型，重复调用安全；缺少本地模型时明确失败.

        Raises:
            ValueError: 模型目录缺少 model.bin.
            RuntimeError: 识别器已关闭.
        """
        async with self._lock:
            if self._closed:
                raise RuntimeError("ASR provider is closed")
            if self._model is not None:
                return
            if not (Path(self._model_path) / "model.bin").is_file():
                raise ValueError(
                    "Set SPEECH_ASR_MODEL_PATH to a downloaded Whisper model"
                )
            factory = self._factory or load_faster_whisper()
            # 加载在线程中执行；调用方取消时等待加载结束再传播，丢弃迟到模型。
            self._model = await finish_cleanup(
                asyncio.create_task(
                    asyncio.to_thread(
                        factory,
                        self._model_path,
                        device=self._device,
                        compute_type=self._compute_type,
                        local_files_only=True,
                        cpu_threads=4,
                    )
                )
            )
            logger.info(
                "[ResidentWhisperASRProvider.start] 识别模型已加载 device=%s compute_type=%s",
                self._device,
                self._compute_type,
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
        _ = self._loaded_model()
        content = await read_wav(self._evidence, audio)
        async with asyncio.timeout(timeout), self._lock:
            # 等锁期间可能已经关闭，进入临界区后重新确认模型仍可用。
            model = self._loaded_model()
            # 原生推理无法强行中断：取消或超时先等线程结束，避免与释放并发。
            text, language = await finish_cleanup(
                asyncio.create_task(
                    asyncio.to_thread(transcribe_wav, model, content, self._language)
                )
            )
        return TranscriptResult(text, language, True)

    def _loaded_model(self) -> WhisperModel:
        """返回可用模型，未加载或已关闭时明确失败."""
        if self._model is None or self._closed:
            raise RuntimeError("ASR model is not loaded")
        return self._model

    async def close(self) -> None:
        """拒绝新请求，等待进行中的推理结束后释放模型，重复调用安全."""
        self._closed = True
        async with self._lock:
            if self._model is not None:
                self._model = None
                logger.info("[ResidentWhisperASRProvider.close] 识别模型已释放")
