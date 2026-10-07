# speech/remote_worker.py
"""无声卡服务器上的单次语音作业，EOF、断线和期限都会回收推理子进程."""

import asyncio
import json
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from domain.validation import (
    finite_float,
    nonempty_string,
    positive_int,
    string_key_dict,
)
from providers.asr.whisper import WhisperASRProvider
from providers.audio.wav import save_wav
from providers.tts.piper import PiperTTSProvider
from storage.evidence import EvidenceStore


@dataclass(frozen=True)
class RemoteRequest:
    """已验证的内部单次请求，不允许远程指定可执行文件或本地路径."""

    operation: str
    content: bytes
    timeout_s: float
    option: str


async def execute(request: RemoteRequest) -> bytes:
    """仅运行识别或文件合成，服务器不实例化录音器或播放器."""
    store = EvidenceStore()
    if request.operation == "asr":
        audio = await save_wav(store, request.content, time.time())
        provider = WhisperASRProvider(
            store,
            os.environ.get("SPEECH_ASR_MODEL_PATH", "models/whisper-base"),
            request.option,
            os.environ.get("SPEECH_ASR_DEVICE", "cpu"),
            os.environ.get("SPEECH_ASR_COMPUTE_TYPE", "int8"),
        )
        result = await provider.transcribe(audio, request.timeout_s)
        return json.dumps(
            {
                "text": result.text,
                "language": result.language,
                "is_final": result.is_final,
            }
        ).encode()
    tts = PiperTTSProvider(
        store,
        os.environ.get(
            "SPEECH_TTS_MODEL_PATH", "models/piper/zh_CN-huayan-medium.onnx"
        ),
        os.environ.get("SPEECH_TTS_VOICE", "huayan"),
    )
    audio = await tts.synthesize(
        request.content.decode("utf-8"), request.option, request.timeout_s
    )
    return await store.read(audio.evidence_id)


async def read_request(reader: asyncio.StreamReader) -> RemoteRequest:
    """有界读取输入，参数通过实际校验后才进入模型."""
    async with asyncio.timeout(10):
        line = await reader.readline()
        if len(line) > 4096:
            raise ValueError("Oversized request header")
        header = string_key_dict(cast(object, json.loads(line)), "request")
        if set(header) != {"operation", "size", "timeout_s", "option"}:
            raise ValueError("Unexpected speech request fields")
        operation = header.get("operation")
        if operation not in {"asr", "tts"} or not isinstance(operation, str):
            raise ValueError("Unknown speech operation")
        size = positive_int(header.get("size"), "size")
        if size > (32 * 1024 * 1024 if operation == "asr" else 8000):
            raise ValueError("Oversized speech input")
        timeout = finite_float(header.get("timeout_s"), "timeout_s")
        option = nonempty_string(header.get("option"), "option")
        if not 0 < timeout <= 300 or len(option) > 128:
            raise ValueError("Invalid speech deadline or option")
        return RemoteRequest(operation, await reader.readexactly(size), timeout, option)


async def serve(
    reader: asyncio.StreamReader,
    execute_request: Callable[[RemoteRequest], Awaitable[bytes]] = execute,
) -> tuple[str, bytes]:
    """监控输入 EOF，取消后等待子进程回收，再返回取消确认."""
    job: asyncio.Task[bytes] | None = None
    disconnected: asyncio.Task[bytes] | None = None
    interrupted = False
    try:
        request = await read_request(reader)
        # ========== Step1: 推理与连接状态并行等待，断线优先拒绝结果 ==========
        async with asyncio.timeout(request.timeout_s):

            async def run() -> bytes:
                """把可注入执行器包为独立作业."""
                return await execute_request(request)

            job = asyncio.create_task(run())
            disconnected = asyncio.create_task(reader.read(1))
            _ = await asyncio.wait(
                (job, disconnected), return_when=asyncio.FIRST_COMPLETED
            )
            if disconnected.done():
                interrupted = True
                return "cancelled", b""
            return "ok", job.result()
    except asyncio.CancelledError:
        interrupted = True
        return "cancelled", b""
    except TimeoutError:
        interrupted = True
        return "timed_out", b""
    except Exception:
        return "failed", b""
    finally:
        # ========== Step2: 先回收工作进程，不能先向客户端声称取消成功 ==========
        tasks = [task for task in (job, disconnected) if task is not None]
        for task in tasks:
            _ = task.cancel()
        _ = await asyncio.gather(*tasks, return_exceptions=True)
        if interrupted and job is not None and not job.cancelled():
            failure = job.exception()
            if failure is not None and not isinstance(failure, TimeoutError):
                raise RuntimeError("Remote job cleanup failed") from failure


async def main(
    execute_request: Callable[[RemoteRequest], Awaitable[bytes]] = execute,
) -> None:
    """通过 SSH 管道接收一次请求，所有响应均有明确长度和状态."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=8192)
    input_transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )
    output_transport, protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout.buffer
    )
    writer = asyncio.StreamWriter(output_transport, protocol, None, loop)
    serving = asyncio.create_task(serve(reader, execute_request))
    stopping = False

    def stop() -> None:
        """只请求一次取消，重复断线信号不能打断进程回收."""
        nonlocal stopping
        if not stopping and not serving.done():
            stopping = True
            _ = serving.cancel()

    for sig in (signal.SIGHUP, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    try:
        status, content = await serving
        if len(content) > 32 * 1024 * 1024:
            status, content = "failed", b""
        writer.write(
            json.dumps({"status": status, "size": len(content)}).encode()
            + b"\n"
            + content
        )
        async with asyncio.timeout(5):
            await writer.drain()
    finally:
        input_transport.close()
        writer.close()
        for sig in (signal.SIGHUP, signal.SIGTERM):
            _ = loop.remove_signal_handler(sig)


if __name__ == "__main__":
    asyncio.run(main())
