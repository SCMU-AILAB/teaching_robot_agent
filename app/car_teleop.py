# app/car_teleop.py
"""麦克纳姆底盘键盘遥控与单电机标定工具，Mac 与 Jetson 终端通用.

用法::

    uv run --extra car python -m app.car_teleop /dev/cu.wchusbserialXXXX
    uv run --extra car python -m app.car_teleop PORT --motor 1 --duty 300 --seconds 2
"""

import argparse
import logging
import select
import sys
import termios
import time
import tty
from dataclasses import dataclass, replace

from robot.car_link import (
    Ack,
    CarFrame,
    CarLink,
    Telemetry,
    motor,
    open_serial,
    ping,
    velocity,
)
from robot.car_link import stop as stop_frame

SEND_PERIOD_S = 0.05
SPEED_STEP = 100
DEFAULT_SPEED = 500
HELP = (
    "W/S 前进/后退  A/D 左移/右移  Q/E 逆时针/顺时针旋转  空格 停车  +/- 调速  "
    + "X 退出（按键切换并保持，不需要按住）"
)


class TeleopArguments(argparse.Namespace):
    """命令行参数的类型声明."""

    port: str = ""
    motor: int | None = None
    duty: int = 300
    seconds: float = 2.0


@dataclass(frozen=True)
class DriveState:
    """当前保持的运动方向与速度大小（千分比）."""

    vx: int = 0
    vy: int = 0
    wz: int = 0
    speed: int = DEFAULT_SPEED

    @property
    def moving(self) -> bool:
        """是否有非零速度."""
        return (self.vx, self.vy, self.wz) != (0, 0, 0)


def apply_key(state: DriveState, key: str) -> DriveState | None:
    """按键更新运动状态；返回 None 表示退出，未知按键保持原状态."""
    key = key.lower()
    s = state.speed
    motions = {
        "w": (s, 0, 0),
        "s": (-s, 0, 0),
        "a": (0, s, 0),
        "d": (0, -s, 0),
        "q": (0, 0, s),
        "e": (0, 0, -s),
        " ": (0, 0, 0),
    }
    if key in ("x", "\x03"):
        return None
    if key in motions:
        vx, vy, wz = motions[key]
        return replace(state, vx=vx, vy=vy, wz=wz)
    if key in ("+", "="):
        return _rescale(state, min(1000, s + SPEED_STEP))
    if key in ("-", "_"):
        return _rescale(state, max(SPEED_STEP, s - SPEED_STEP))
    return state


def _sign(value: int) -> int:
    """返回 -1、0 或 1."""
    return (value > 0) - (value < 0)


def _rescale(state: DriveState, speed: int) -> DriveState:
    """改变速度大小并同步缩放当前运动方向."""
    return DriveState(
        speed * _sign(state.vx), speed * _sign(state.vy), speed * _sign(state.wz), speed
    )


def describe(frames: list[CarFrame]) -> str | None:
    """把最新遥测与异常应答格式化为一行状态文本."""
    text: str | None = None
    for frame in frames:
        if isinstance(frame, Telemetry):
            text = f"state={frame.state} enc={frame.encoders} duty={frame.duties}"
        elif isinstance(frame, Ack) and frame.code != 0:
            text = f"命令 {frame.seq} 被拒绝，code={frame.code}"
    return text


def run_teleop(link: CarLink) -> None:
    """终端原始模式下读取按键，以 20 Hz 持续发送当前速度."""
    print(HELP, flush=True)
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    state = DriveState()
    try:
        _ = tty.setcbreak(fd)
        while True:
            readable, _, _ = select.select([sys.stdin], [], [], SEND_PERIOD_S)
            if readable:
                updated = apply_key(state, sys.stdin.read(1))
                if updated is None:
                    break
                if state.moving and not updated.moving:
                    link.send(stop_frame(link.next_seq()))
                state = updated
            seq = link.next_seq()
            # 停车时只发心跳，不刷新运动看门狗
            link.send(
                velocity(seq, state.vx, state.vy, state.wz)
                if state.moving
                else ping(seq)
            )
            status = describe(link.poll())
            if status is not None:
                print(
                    f"\r[v={state.vx},{state.vy},{state.wz} speed={state.speed}] "
                    + f"{status}\033[K",
                    end="",
                    flush=True,
                )
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        print()


def run_motor_test(link: CarLink, index: int, duty: int, seconds: float) -> None:
    """单电机运行指定时间后停车，报告各编码器计数变化."""
    first: Telemetry | None = None
    last: Telemetry | None = None
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        link.send(motor(link.next_seq(), index, duty))
        for frame in link.poll():
            if isinstance(frame, Telemetry):
                first = first or frame
                last = frame
            elif isinstance(frame, Ack) and frame.code != 0:
                print(f"命令 {frame.seq} 被拒绝，code={frame.code}")
        time.sleep(SEND_PERIOD_S)
    link.send(stop_frame(link.next_seq()))
    if first is None or last is None:
        print("没有收到遥测：确认接的是 UART2 口、小车已上电。")
        return
    delta = tuple(b - a for a, b in zip(first.encoders, last.encoders, strict=True))
    print(f"M{index} duty={duty} 运行 {seconds:.1f}s，编码器变化 M1..M4 = {delta}")


def main() -> None:
    """解析参数，打开串口并进入遥控或单电机标定模式."""
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="麦克纳姆底盘键盘遥控与单电机标定")
    _ = parser.add_argument("port", help="小车 UART2 口对应的串口设备")
    _ = parser.add_argument("--motor", type=int, choices=range(1, 5))
    _ = parser.add_argument("--duty", type=int, default=300)
    _ = parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args(namespace=TeleopArguments())
    link = CarLink(open_serial(args.port))
    try:
        if args.motor is None:
            run_teleop(link)
        else:
            run_motor_test(link, args.motor, args.duty, args.seconds)
    finally:
        link.close()


if __name__ == "__main__":
    main()
