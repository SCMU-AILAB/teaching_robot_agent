# app/car_calibrate.py
"""底盘里程计标定：按固定速度行驶，量出实际距离或角度，换算 CAR_COUNTS_PER_* 常数.

小车会真实移动，须在自己的终端运行并确保前方有空地（需要交互输入，不能经 Claude Code 的 `!` 执行）::

    uv run --extra car python -m app.car_calibrate PORT forward --permille 300 --seconds 2
    uv run --extra car python -m app.car_calibrate PORT turn --permille 300 --seconds 2
    uv run --extra car python -m app.car_calibrate PORT lateral --permille 300 --seconds 2
"""

import argparse
import math
import time

from robot.car_link import CarLink, Telemetry, open_serial, stop, velocity

SEND_PERIOD_S = 0.05
_ENV = {
    "forward": "CAR_COUNTS_PER_METER",
    "lateral": "CAR_COUNTS_PER_METER_LATERAL",
    "turn": "CAR_COUNTS_PER_RADIAN",
}


class CalibrateArguments(argparse.Namespace):
    """命令行参数的类型声明."""

    port: str = ""
    mode: str = "forward"
    permille: int = 300
    seconds: float = 2.0


def axis_counts(
    start: tuple[int, int, int, int], end: tuple[int, int, int, int]
) -> dict[str, float]:
    """由 M1..M4 累计计数的变化求三个运动方向的轮组合计数.

    轮位：左前 M4、右前 M1、左后 M3、右后 M2；组合与 CarAdapter 的里程计一致。

    Returns:
        forward、lateral（向左为正）、turn（逆时针为正）三项计数.
    """
    m1, m2, m3, m4 = (
        (new - old + (1 << 31)) % (1 << 32) - (1 << 31)
        for new, old in zip(end, start, strict=True)
    )
    fl, fr, rl, rr = m4, m1, m3, m2
    return {
        "forward": (fl + fr + rl + rr) / 4,
        "lateral": (-fl + fr + rl - rr) / 4,
        "turn": (-fl + fr - rl + rr) / 4,
    }


def _latest_telemetry(link: CarLink, timeout_s: float) -> Telemetry:
    """读取到最新一帧遥测为止.

    Raises:
        RuntimeError: 超时没有遥测.
    """
    deadline = time.monotonic() + timeout_s
    latest: Telemetry | None = None
    while time.monotonic() < deadline:
        for frame in link.poll():
            if isinstance(frame, Telemetry):
                latest = frame
        if latest is not None:
            return latest
    raise RuntimeError("小车没有遥测，检查串口与电源")


def _settle(link: CarLink) -> Telemetry:
    """停车后等待编码器连续两帧不变."""
    previous = _latest_telemetry(link, 1.0)
    still = 0
    deadline = time.monotonic() + 2.0
    while still < 2 and time.monotonic() < deadline:
        current = _latest_telemetry(link, 1.0)
        still = still + 1 if current.encoders == previous.encoders else 0
        previous = current
    return previous


def run(arguments: CalibrateArguments) -> None:
    """行驶一段后询问实测值并打印环境变量."""
    if not 0 < arguments.permille <= 600 or not 0 < arguments.seconds <= 10:
        raise SystemExit("--permille 取 1..600，--seconds 取 (0, 10]")
    command = {
        "forward": (arguments.permille, 0, 0),
        "lateral": (0, arguments.permille, 0),
        "turn": (0, 0, arguments.permille),
    }[arguments.mode]
    link = CarLink(open_serial(arguments.port))
    try:
        link.send(stop(link.next_seq()))
        start = _settle(link)
        print(
            f"开始：{arguments.mode} 千分比 {arguments.permille}，{arguments.seconds} 秒"
        )
        ends_at = time.monotonic() + arguments.seconds
        while time.monotonic() < ends_at:
            link.send(velocity(link.next_seq(), *command))
            for frame in link.poll():
                if isinstance(frame, Telemetry) and frame.state == 3:
                    raise RuntimeError("底盘报告堵转或编码器故障")
            time.sleep(SEND_PERIOD_S)
        link.send(stop(link.next_seq()))
        end = _settle(link)
    finally:
        link.close()
    counts = axis_counts(start.encoders, end.encoders)[arguments.mode]
    print(f"轮组合计数：{counts:.0f}")
    if arguments.mode == "turn":
        measured = float(input("实际逆时针转了多少度？顺时针填负数："))
        value = counts / math.radians(measured)
    else:
        prompt = (
            "实际前进了多少米？"
            if arguments.mode == "forward"
            else "实际向左移了多少米？"
        )
        value = counts / float(input(prompt))
    print(f"export {_ENV[arguments.mode]}={value:.1f}")


def main() -> None:
    """解析参数并运行."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("port")
    _ = parser.add_argument("mode", choices=sorted(_ENV))
    _ = parser.add_argument("--permille", type=int, default=300)
    _ = parser.add_argument("--seconds", type=float, default=2.0)
    run(parser.parse_args(namespace=CalibrateArguments()))


if __name__ == "__main__":
    main()
