# providers/asr/worker.py
"""真实离线识别进程，使用 VAD 排除静音，不在模块导入时加载模型."""

import argparse
import json
import sys
from dataclasses import dataclass

from providers.asr.whisper_engine import load_faster_whisper, transcribe_wav
from providers.audio.wav import decode_wav


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
        model = load_faster_whisper()(
            args.model,
            device=args.device,
            compute_type=args.compute_type,
            local_files_only=True,
            cpu_threads=4,
        )
        # ========== Step2: VAD 过滤静音，再消费全部识别段取得最终文本 ==========
        text, language = transcribe_wav(model, content, args.language)
        print(json.dumps({"text": text, "language": language, "is_final": True}))
    except Exception as error:
        print(f"ASR failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
