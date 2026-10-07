# speech/remote.py
"""基于 SSH 标准输入输出的内部语音传输，不新增 HTTP 或设备端口."""

import asyncio
import json
import re
import shlex
from dataclasses import dataclass
from typing import cast

from domain.validation import (
    finite_float,
    nonempty_string,
    positive_int,
    string_key_dict,
)
from speech._lifecycle import finish_cleanup
from speech._process import managed_process

_MAX_AUDIO = 32 * 1024 * 1024


@dataclass(frozen=True)
class SpeechServer:
    """服务器地址来自环境；认证使用已有 SSH 配置，不存储密码."""

    host: str
    user: str
    port: int
    directory: str

    def command(self) -> tuple[str, ...]:
        """验证连接参数，远端命令按 shell 规则逐项引用."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", self.host):
            raise ValueError("Invalid speech SSH host")
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", self.user):
            raise ValueError("Invalid speech SSH user")
        if positive_int(self.port, "port") > 65535:
            raise ValueError("Invalid speech SSH port")
        directory = nonempty_string(self.directory, "server_directory")
        if not directory.startswith("/") or "\n" in directory:
            raise ValueError("Speech server directory must be absolute")
        command = f"cd {shlex.quote(directory)} && exec " + shlex.join(
            (f"{directory}/.venv/bin/python", "-m", "speech.remote_worker")
        )
        return (
            "ssh",
            "-T",
            "-p",
            str(self.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=2",
            f"{self.user}@{self.host}",
            command,
        )


@dataclass(frozen=True)
class _Reply:
    """内部传输结果，不能当作 HTTP 或领域错误模型."""

    status: str
    content: bytes


async def _read_reply(process: asyncio.subprocess.Process, limit: int) -> _Reply:
    """先校验有界响应头，再读取声明长度的结果并等待远端退出."""
    assert process.stdout is not None
    line = await process.stdout.readline()
    if len(line) > 4096:
        raise ValueError("Oversized speech response header")
    header = string_key_dict(cast(object, json.loads(line)), "response")
    status = header.get("status")
    size = header.get("size")
    if (
        status not in {"ok", "cancelled", "failed", "timed_out"}
        or not isinstance(status, str)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or not 0 <= size <= limit
        or (status != "ok" and size != 0)
    ):
        raise ValueError("Invalid speech server response")
    content = await process.stdout.readexactly(size)
    if await process.stdout.read(1):
        raise ValueError("Unexpected trailing speech response")
    if await process.wait() != 0:
        raise RuntimeError("Remote speech worker exited unexpectedly")
    return _Reply(status, content)


class SSHSpeechTransport:
    """每次请求一个受限服务器作业，取消时先关闭输入并等待回收确认."""

    def __init__(self, _server: SpeechServer) -> None:
        """只构造参数，不在导入或构造期间连接服务器."""
        self._command: tuple[str, ...] = _server.command()

    async def request(
        self, operation: str, content: bytes, timeout_s: float, option: str
    ) -> bytes:
        """发送音频或文本，保留取消和超时，不回退到本地推理.

        Args:
            operation: 仅允许 asr 或 tts。
            content: WAV 音频或 UTF-8 文本。
            timeout_s: 含连接和传输的客户端期限，秒。
            option: ASR 语言或 TTS 音色。

        Returns:
            ASR JSON 或 TTS WAV。

        Raises:
            TimeoutError: 请求超时。
            RuntimeError: 远程执行失败或取消清理未确认。
            asyncio.CancelledError: 调用方取消，已取得远端结束确认。
        """
        timeout = finite_float(timeout_s, "timeout_s")
        if operation not in {"asr", "tts"} or not 0 < timeout <= 300:
            raise ValueError("Invalid remote speech operation or timeout")
        if not content or len(content) > (_MAX_AUDIO if operation == "asr" else 8000):
            raise ValueError("Invalid remote speech request size")
        option = nonempty_string(option, "option")
        if len(option) > 128:
            raise ValueError("Speech option too long")
        header = (
            json.dumps(
                {
                    "operation": operation,
                    "size": len(content),
                    "timeout_s": timeout,
                    "option": option,
                }
            ).encode()
            + b"\n"
        )
        # ========== Step1: 输入管道保持打开，以 EOF 作为取消信号 ==========
        async with asyncio.timeout(timeout):
            async with managed_process(self._command) as process:
                assert process.stdin is not None
                reply_task = asyncio.create_task(
                    _read_reply(
                        process, _MAX_AUDIO if operation == "tts" else 128 * 1024
                    )
                )
                try:
                    process.stdin.write(header + content)
                    await process.stdin.drain()
                    reply = await asyncio.shield(reply_task)
                except BaseException:
                    # ========== Step2: 先请求远端取消并等确认，再传播外部取消 ==========
                    await finish_cleanup(
                        asyncio.create_task(self._cancel(process, reply_task))
                    )
                    raise
                if reply.status == "timed_out":
                    raise TimeoutError("Remote speech inference timed out")
                if reply.status != "ok":
                    raise RuntimeError(f"Remote speech request {reply.status}")
                return reply.content

    async def _cancel(
        self, process: asyncio.subprocess.Process, reply: asyncio.Task[_Reply]
    ) -> None:
        """EOF 让服务器回收子进程；连接失效时明确表示未确认远端状态."""
        assert process.stdin is not None
        process.stdin.close()
        try:
            async with asyncio.timeout(8):
                _ = await asyncio.shield(reply)
        except BaseException:
            _ = reply.cancel()
            _ = await asyncio.gather(reply, return_exceptions=True)
            raise RuntimeError(
                "Remote speech cleanup not confirmed; do not assume the job stopped"
            ) from None
