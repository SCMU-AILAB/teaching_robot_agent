# providers/asr/worker.py
"""真实离线识别进程，使用 VAD 排除静音，不在模块导入时加载模型."""

import argparse
import io
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast

from providers.audio.wav import decode_wav


class _Segment(Protocol):
    """识别引擎的文本段."""

    text: str


class _Info(Protocol):
    """识别引擎提供的语言信息."""

    language: str


class _Model(Protocol):
    """仅声明实际使用的 faster-whisper 调用边界."""

    def transcribe(
        self,
        audio: io.BytesIO,
        *,
        language: str,
        beam_size: int,
        vad_filter: bool,
        condition_on_previous_text: bool,
    ) -> tuple[Iterable[_Segment], _Info]: ...


class _ModelFactory(Protocol):
    """真实模型构造函数的类型边界."""

    def __call__(
        self,
        model_size_or_path: str,
        *,
        device: str,
        compute_type: str,
        local_files_only: bool,
        cpu_threads: int,
    ) -> _Model: ...


@dataclass
class _Options(argparse.Namespace):
    """经 argparse 转换的离线识别配置."""

    model: str = ""
    language: str = "zh"
    device: str = "cpu"
    compute_type: str = "int8"


def main() -> None:
    """从标准输入读取 WAV，输出经校验的最终文本 JSON."""
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--model", required=True)
    _ = parser.add_argument("--language", default="zh")
    _ = parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    _ = parser.add_argument("--compute-type", default="int8")
    args = _Options()
    _ = parser.parse_args(namespace=args)
    try:
        content = sys.stdin.buffer.read(32 * 1024 * 1024 + 1)
        _ = decode_wav(content)
        # ========== Step1: 只从已下载的本地目录加载引擎 ==========
        module = import_module("faster_whisper")
        model = cast(_ModelFactory, module.WhisperModel)(
            args.model,
            device=args.device,
            compute_type=args.compute_type,
            local_files_only=True,
            cpu_threads=4,
        )
        # ========== Step2: VAD 过滤静音，再消费全部识别段取得最终文本 ==========
        segments, info = model.transcribe(
            io.BytesIO(content),
            language=args.language,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        text = "".join(segment.text for segment in segments).strip()
        print(json.dumps({"text": text, "language": info.language, "is_final": True}))
    except Exception as error:
        print(f"ASR failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
