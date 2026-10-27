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


def _match_score(name: str, wanted: str) -> int:
    """返回名称匹配强度；-1 表示不匹配."""
    lowered = name.casefold()
    target = wanted.casefold()
    if lowered == target:
        return 3
    if lowered.startswith(target):
        return 2
    if target in lowered:
        return 1
    return -1


class _DeviceQuery(Protocol):
    """sounddevice 模块中解析设备所需的最小接口."""

    def query_devices(self) -> list[dict[str, object]]: ...


def resolve_device(driver: _DeviceQuery, text: str, *, output: bool) -> int | None:
    """按设备名称解析 sounddevice 序号.

    设备序号由内核枚举顺序决定，拔插后会变化，因此配置只接受名称。
    名称必须唯一匹配到具备对应方向通道的设备，否则明确报错，
    不退回系统默认设备，避免静默选错声卡。

    Args:
        driver: 已导入的 sounddevice 模块。
        text: 环境变量给出的设备名称，空字符串表示系统默认。
        output: 为 True 时按播放通道筛选，否则按录音通道筛选。

    Returns:
        匹配到的设备序号；名称为空时返回 None。

    Raises:
        ValueError: 没有匹配设备，或名称同时匹配多个设备。
    """
    wanted = text.strip()
    if not wanted:
        return None
    query = driver.query_devices
    channel_key = "max_output_channels" if output else "max_input_channels"
    candidates: list[tuple[int, int, str]] = []
    # ========== Step1: 只保留具备目标方向通道的设备 ==========
    for index, info in enumerate(query()):
        name = str(info.get("name", ""))
        channels = info.get(channel_key)
        if not isinstance(channels, int) or channels <= 0:
            continue
        score = _match_score(name, wanted)
        if score >= 0:
            candidates.append((score, index, name))
    # ========== Step2: 取最强匹配，重叠时拒绝猜测 ==========
    if not candidates:
        direction = "playback" if output else "capture"
        raise ValueError(f"No {direction} device matches {wanted!r}")
    best = max(score for score, _index, _name in candidates)
    winners = [(index, name) for score, index, name in candidates if score == best]
    if len(winners) > 1:
        names = ", ".join(f"{index}:{name}" for index, name in winners)
        raise ValueError(f"Ambiguous device name {wanted!r}: {names}")
    return winners[0][0]


def run_audio(mode: str, device: str, rate: int, duration: float) -> None:
    """采集或播放 PCM；READY 代表设备已启动，完成代表驱动已停止."""
    driver = import_module("sounddevice")
    if mode == "devices":
        print(cast(Callable[[], object], driver.query_devices)())
        return
    # 可选依赖只在工作进程内导入，边界处收敛为最小接口，不传播 ModuleType。
    query = cast(_DeviceQuery, cast(object, driver))
    device_index = resolve_device(query, device, output=mode == "play")
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
            device=device_index,
        )
    else:
        stream = cast(_StreamFactory, driver.RawInputStream)(
            samplerate=rate, channels=1, dtype="int16", device=device_index
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
    try:
        run_audio(mode, device_text, args.rate, args.duration)
    except Exception as error:
        print(f"Audio device failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
