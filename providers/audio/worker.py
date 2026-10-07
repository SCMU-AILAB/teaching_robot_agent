# providers/audio/worker.py
"""隔离 PortAudio 的真实设备工作进程，只在命令入口打开设备."""

import argparse
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from types import FrameType
from typing import Protocol, cast

from providers.audio.wav import PcmAudio, decode_wav, encode_wav


class _Stream(Protocol):
    """PortAudio 阻塞流所使用的最小接口."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def abort(self) -> None: ...
    def close(self) -> None: ...
    def read(self, frames: int) -> tuple[object, bool]: ...
    def write(self, data: bytes) -> bool: ...


class _StreamFactory(Protocol):
    """可选依赖的构造边界，库只在工作进程内按需导入."""

    def __call__(
        self, *, samplerate: int, channels: int, dtype: str, device: str | int | None
    ) -> _Stream: ...


class _CancelledError(Exception):
    """终止信号使设备先执行 abort 和 close，再确认退出."""


@dataclass
class _Options(argparse.Namespace):
    """经 argparse 转换后的工作进程参数."""

    mode: str = "devices"
    device: str = ""
    rate: int = 16000
    duration: float = 15


def run_audio(mode: str, device: str | int | None, rate: int, duration: float) -> None:
    """采集或播放 PCM；READY 代表设备已启动，完成代表驱动已停止."""
    driver = import_module("sounddevice")
    if mode == "devices":
        print(cast(Callable[[], object], driver.query_devices)())
        return
    stopping = False

    def finish(_signum: int, _frame: FrameType | None) -> None:
        """主动结束保留已录帧，不把取消保存为有效输入."""
        nonlocal stopping
        stopping = True

    def cancel(_signum: int, _frame: FrameType | None) -> None:
        """打断阻塞读写，进入统一设备清理."""
        raise _CancelledError

    _ = signal.signal(signal.SIGINT, finish)
    _ = signal.signal(signal.SIGTERM, cancel)
    audio = None
    if mode == "play":
        audio = decode_wav(sys.stdin.buffer.read(32 * 1024 * 1024 + 1))
        stream = cast(_StreamFactory, driver.RawOutputStream)(
            samplerate=audio.sample_rate,
            channels=audio.channels,
            dtype="int16",
            device=device,
        )
    else:
        stream = cast(_StreamFactory, driver.RawInputStream)(
            samplerate=rate, channels=1, dtype="int16", device=device
        )
    chunks: list[bytes] = []
    cancelled = False
    try:
        stream.start()
        print("READY", flush=True)
        if audio is None:
            deadline = time.monotonic() + duration
            while not stopping and time.monotonic() < deadline:
                data, overflow = stream.read(max(1, rate // 50))
                if overflow:
                    raise RuntimeError("Audio input overflow")
                # sounddevice 返回 CFFI buffer，在边界实际转换而不传播未知类型。
                chunks.append(bytes(memoryview(cast(bytes, data))))
        else:
            step = max(1, audio.sample_rate // 50) * audio.channels * 2
            for offset in range(0, len(audio.frames), step):
                if stream.write(audio.frames[offset : offset + step]):
                    raise RuntimeError("Audio output underflow")
            stream.stop()  # 等待驱动排空播放缓冲，不能用预计时长代替完成反馈。
    except _CancelledError:
        cancelled = True
    finally:
        try:
            stream.abort()
        finally:
            stream.close()
    if cancelled:
        print("STOPPED", flush=True)
        raise SystemExit(2)
    if audio is None:
        _ = sys.stdout.buffer.write(encode_wav(PcmAudio(b"".join(chunks), rate, 1)))
        _ = sys.stdout.buffer.flush()
    else:
        print("COMPLETED", flush=True)


def main() -> None:
    """解析受控参数，错误仅输出类型，不输出音频或设备敏感路径."""
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("mode", choices=("record", "play", "devices"))
    _ = parser.add_argument("--device", default="")
    _ = parser.add_argument("--rate", type=int, default=16000)
    _ = parser.add_argument("--duration", type=float, default=15)
    args = _Options()
    _ = parser.parse_args(namespace=args)
    mode = args.mode
    device_text = args.device
    device = int(device_text) if device_text.isdecimal() else device_text or None
    try:
        run_audio(mode, device, args.rate, args.duration)
    except Exception as error:
        print(f"Audio device failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
