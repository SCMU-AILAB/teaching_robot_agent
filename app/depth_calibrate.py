# app/depth_calibrate.py
"""深度相机安装标定与空隙查看（Jetson 上运行，车不会移动）.

车放在空地上、前方 1 米内没有东西，标定摄像头离地高度与俯仰::

    uv run --extra depth python -m app.depth_calibrate mount

按 robot.env 中的安装参数持续打印各方向空隙，用于摆放障碍物核对::

    uv run --extra depth python -m app.depth_calibrate watch --seconds 20
"""

import argparse
import math
import time

from app.depth import depth_geometry_from_env
from perception.depth.camera import DepthSensor, capture_depth, find_depth_device
from perception.depth.geometry import DepthGeometry, estimate_mount


class CalibrateArguments(argparse.Namespace):
    """命令行参数的类型声明."""

    mode: str = "mount"
    device: str = ""
    seconds: float = 20.0


def calibrate_mount(device: str) -> None:
    """采集一帧并拟合地面，打印可写入 robot.env 的配置."""
    depth = capture_depth(device)
    # 只用到内参，安装参数取占位值
    estimate = estimate_mount(
        depth, DepthGeometry(camera_height_m=0.2, camera_pitch_rad=0.0)
    )
    print(f"地面点 {estimate.inliers} 个")
    print(f"DEPTH_CAMERA_HEIGHT_M={estimate.camera_height_m:.3f}")
    print(f"DEPTH_CAMERA_PITCH_DEG={math.degrees(estimate.camera_pitch_rad):.1f}")
    if math.isinf(estimate.blind_distance_m):
        print("盲区：地面完全不在视野内，车前贴地的东西都看不见")
    else:
        print(f"盲区：车前 {estimate.blind_distance_m:.2f} 米内贴地的东西看不见")


def watch(device: str, seconds: float) -> None:
    """每 0.5 秒打印一次前方与各方向空隙."""
    sensor = DepthSensor(depth_geometry_from_env(), device)
    sensor.start()
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(0.5)
            reading = sensor.latest()
            if reading is None:
                print("暂无深度数据")
                continue
            sides = "  ".join(
                f"{math.degrees(angle):+.0f}°:{value:.2f}"
                for angle, value in reading.headings
            )
            print(
                f"前方 {reading.ahead_m:.2f} 米  {sides}  "
                + f"中部有效 {reading.center_valid_ratio:.0%}"
            )
    finally:
        sensor.close()


def main() -> None:
    """解析命令行并执行."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("mode", choices=("mount", "watch"))
    _ = parser.add_argument("--device", default="", help="深度节点，缺省自动查找")
    _ = parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args(namespace=CalibrateArguments())
    device = args.device or find_depth_device()
    if args.mode == "mount":
        calibrate_mount(device)
    else:
        watch(device, args.seconds)


if __name__ == "__main__":
    main()
