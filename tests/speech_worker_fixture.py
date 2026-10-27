# tests/speech_worker_fixture.py
"""仅供自动化测试的进程替身，不访问设备，不作为实际语音适配器."""

import json
import os
import signal
import sys
import time
from pathlib import Path
from types import FrameType

from providers.audio.wav import PcmAudio, encode_wav


def write_marker(path: str) -> None:
    """先写临时文件再原子替换，读侧要么看不到文件，要么读到完整内容."""
    temporary = Path(f"{path}.{os.getpid()}.tmp")
    _ = temporary.write_text(str(os.getpid()), encoding="ascii")
    os.replace(temporary, path)


def main() -> None:
    """复现就绪、自然结束、主动停止、停止失败和推理挂起时序."""
    scenario, marker, *arguments = sys.argv[1:]
    write_marker(marker)
    stopped = False
    completed = False

    def finish(_number: int, _frame: FrameType | None) -> None:
        """主动结束采集."""
        nonlocal stopped
        stopped = True

    def terminate(_number: int, _frame: FrameType | None) -> None:
        """模拟设备清理反馈，错误反馈用于验证不能伪装停止成功."""
        if completed:
            raise SystemExit(0)
        print(
            "UNKNOWN" if scenario in {"bad-stop", "early-bad-stop"} else "STOPPED",
            flush=True,
        )
        raise SystemExit(2)

    _ = signal.signal(signal.SIGINT, finish)
    _ = signal.signal(signal.SIGTERM, terminate)
    mode = arguments[0]
    if scenario == "fail":
        raise SystemExit(1)
    if mode in {"record", "play"}:
        if mode == "play":
            _ = sys.stdin.buffer.read()
        if scenario in {"early-stop", "early-bad-stop"}:
            write_marker(marker + ".device")
            time.sleep(60)
        print("READY", flush=True)
        if scenario == "completed-before-exit":
            print("COMPLETED", flush=True)
            completed = True
            os.close(sys.stdout.fileno())
            write_marker(marker + ".device")
            time.sleep(60)
        delay = (
            float(arguments[arguments.index("--duration") + 1])
            if mode == "record"
            else 0.05
        )
        if scenario in {"hang", "bad-stop"}:
            delay = 60
        deadline = time.monotonic() + delay
        while not stopped and time.monotonic() < deadline:
            time.sleep(0.005)
        if mode == "record":
            frames = b"" if scenario == "empty" else b"\0\0" * 320
            _ = sys.stdout.buffer.write(encode_wav(PcmAudio(frames, 16000, 1)))
            _ = sys.stdout.buffer.flush()
        else:
            print("COMPLETED", flush=True)
    else:
        _ = sys.stdin.buffer.read()
        if scenario == "hang":
            time.sleep(60)
        if scenario == "malformed":
            print(json.dumps({"text": 42, "is_final": True}))
        else:
            print(
                json.dumps(
                    {
                        "text": "" if scenario == "empty" else "识别结果",
                        "language": "zh",
                        "is_final": True,
                    }
                )
            )


if __name__ == "__main__":
    main()
