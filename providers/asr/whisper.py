# providers/asr/whisper.py
"""通过可终止工作进程调用真实离线 faster-whisper，不连接云端服务."""

import json
import sys
from pathlib import Path
from typing import cast

from domain.services import AudioReference, TranscriptResult
from domain.validation import finite_float, nonempty_string, string_key_dict
from providers.audio.wav import read_wav
from speech._process import run_process
from storage.evidence import EvidenceStore


class WhisperASRProvider:
    """仅接受已下载模型目录，取消或超时会结束本次推理进程."""

    def __init__(
        self,
        _evidence: EvidenceStore,
        _model_path: str,
        _language: str = "zh",
        _compute_device: str = "cpu",
        _compute_type: str = "int8",
        _worker_command: tuple[str, ...] | None = None,
    ) -> None:
        """注入离线模型位置和计算设备，不在构造期间加载模型."""
        self._evidence: EvidenceStore = _evidence
        self._model: str = nonempty_string(_model_path, "model_path")
        self._language: str = nonempty_string(_language, "language")
        if _compute_device not in {"cpu", "cuda"}:
            raise ValueError("ASR compute device must be cpu or cuda")
        self._device: str = _compute_device
        self._compute_type: str = nonempty_string(_compute_type, "compute_type")
        self._command: tuple[str, ...] = _worker_command or (
            sys.executable,
            "-m",
            "providers.asr.worker",
        )

    async def transcribe(
        self, audio: AudioReference, timeout_s: float
    ) -> TranscriptResult:
        """识别最终文本；无语音返回空文本，异常、超时和取消保持明确语义."""
        timeout = finite_float(timeout_s, "timeout_s")
        if timeout <= 0:
            raise ValueError("ASR timeout must be positive")
        if not Path(self._model).is_dir():
            raise ValueError("ASR model directory is missing; download the model first")
        content = await read_wav(self._evidence, audio)
        command = self._command + (
            "--model",
            self._model,
            "--language",
            self._language,
            "--device",
            self._device,
            "--compute-type",
            self._compute_type,
        )
        result = await run_process(command, content, timeout)
        if len(result) > 128 * 1024:
            raise ValueError("Oversized ASR response")
        value = string_key_dict(cast(object, json.loads(result)), "ASR result")
        text: object = value.get("text")
        language: object = value.get("language")
        final: object = value.get("is_final")
        if (
            not isinstance(text, str)
            or not isinstance(language, str)
            or final is not True
        ):
            raise ValueError("Invalid ASR final transcript")
        return TranscriptResult(text.strip(), language, True)
