# tests/remote_speech_worker_fixture.py
"""远端协议测试进程，复用生产端连接监控，仅替换模型执行."""

import asyncio
import json
import os
import sys
from pathlib import Path

from providers.audio.wav import PcmAudio, encode_wav
from speech._process import run_process
from speech.remote_worker import RemoteRequest, main


async def execute(request: RemoteRequest) -> bytes:
    """注入成功、挂起、失败及非法响应，挂起使用真正的子进程."""
    scenario, directory = sys.argv[1:]
    root = Path(directory)
    _ = (root / "server").write_text(str(os.getpid()), encoding="ascii")
    if scenario == "hang":
        return await run_process(
            (
                sys.executable,
                "-m",
                "tests.speech_worker_fixture",
                "hang",
                str(root / "child"),
                "--model",
                "unused",
            ),
            request.content,
            60,
        )
    if scenario == "fail":
        raise RuntimeError("Injected engine failure")
    if scenario == "invalid":
        return b"invalid"
    if request.operation == "tts":
        return encode_wav(PcmAudio(b"\0\0" * 1600, 16000, 1))
    return json.dumps(
        {
            "text": "" if scenario == "empty" else "识别成功",
            "language": "zh",
            "is_final": True,
        }
    ).encode()


if __name__ == "__main__":
    asyncio.run(main(execute))
