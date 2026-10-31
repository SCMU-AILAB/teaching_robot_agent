# perception/depth/camera.py
"""RealSense 深度节点读取：V4L2 直接读 Z16，后台线程按 10 Hz 更新车前空隙."""

import importlib
import logging
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol, cast

import numpy as np
from numpy.typing import NDArray

from perception.depth.geometry import Clearance, DepthGeometry, measure_clearance

logger = logging.getLogger(__name__)

_WIDTH = 640
_HEIGHT = 480
_PIXEL_FORMAT = "Z16 "
# 空隙计算间隔；深度帧约 24 帧/秒，不必每帧计算
_UPDATE_INTERVAL_S = 0.1
_SYSFS_VIDEO = Path("/sys/class/video4linux")


class ClearanceSource(Protocol):
    """提供最新车前空隙；没有可用数据时返回 None."""

    def latest(self) -> Clearance | None:
        """返回最近一次计算的空隙."""
        ...


class _Frame(Protocol):
    """linuxpy 帧：只使用原始字节."""

    def __bytes__(self) -> bytes:
        """返回帧原始字节."""
        ...


class _Device(Protocol):
    """linuxpy 设备中本模块用到的部分."""

    def open(self) -> None:
        """打开设备."""
        ...

    def close(self) -> None:
        """关闭设备."""
        ...

    def __iter__(self) -> Iterator[_Frame]:
        """按到达顺序产出帧."""
        ...

    def set_format(
        self, buffer_type: object, width: int, height: int, pixel_format: str
    ) -> object:
        """设置采集格式."""
        ...


class _BufferTypes(Protocol):
    """linuxpy BufferType 枚举中用到的成员."""

    VIDEO_CAPTURE: object


class _V4L2Module(Protocol):
    """linuxpy.video.device 模块中用到的部分."""

    Device: Callable[[str], _Device]
    BufferType: _BufferTypes


def _open_device(path: str) -> _Device:
    """打开深度节点并设为 640×480 Z16；linuxpy 只能在 Linux 上导入."""
    module = cast(
        _V4L2Module, cast(object, importlib.import_module("linuxpy.video.device"))
    )
    device = module.Device(path)
    device.open()
    _ = device.set_format(
        module.BufferType.VIDEO_CAPTURE, _WIDTH, _HEIGHT, _PIXEL_FORMAT
    )
    return device


def find_depth_device(root: Path | None = None) -> str:
    """找 RealSense 的深度节点：名称含 RealSense 的节点中编号最小的一个.

    D435i 依次暴露深度、深度元数据、红外、红外元数据、彩色、彩色元数据，深度在最前。

    Raises:
        RuntimeError: 没有 RealSense 视频节点。
    """
    nodes: list[tuple[int, str]] = []
    for entry in (root or _SYSFS_VIDEO).glob("video*"):
        try:
            name = (entry / "name").read_text(encoding="utf-8")
        except OSError:
            continue
        if "RealSense" in name and entry.name[5:].isdigit():
            nodes.append((int(entry.name[5:]), f"/dev/{entry.name}"))
    if not nodes:
        raise RuntimeError("No RealSense video node found")
    return min(nodes)[1]


def frame_to_depth(raw: bytes) -> NDArray[np.uint16]:
    """把 Z16 原始字节转成毫米深度图.

    Raises:
        ValueError: 字节数与 640×480 不符。
    """
    if len(raw) != _WIDTH * _HEIGHT * 2:
        raise ValueError("Unexpected Z16 frame size")
    return np.frombuffer(raw, dtype="<u2").reshape(_HEIGHT, _WIDTH).astype(np.uint16)


def capture_depth(path: str, frames: int = 15) -> NDArray[np.uint16]:
    """同步采集若干帧，返回最后一帧；供标定工具使用，前几帧曝光未稳定."""
    device = _open_device(path)
    try:
        last = b""
        for index, frame in enumerate(device, start=1):
            last = bytes(frame)
            if index >= frames:
                break
        return frame_to_depth(last)
    finally:
        device.close()


class DepthSensor:
    """后台读取深度并计算空隙；模块导入与构造时不打开设备."""

    def __init__(self, _geometry: DepthGeometry, _device: str | None = None) -> None:
        """注入几何参数与可选的设备路径，缺省时自动查找."""
        self._geometry: DepthGeometry = _geometry
        self._device_path: str | None = _device
        self._lock: threading.Lock = threading.Lock()
        self._latest: Clearance | None = None
        self._stopping: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """打开设备并启动读取线程，重复调用安全.

        Raises:
            RuntimeError: 找不到设备。
            OSError: 设备无法打开。
        """
        if self._thread is not None:
            return
        path = self._device_path or find_depth_device()
        device = _open_device(path)
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._run, args=(device,), name="depth-sensor", daemon=True
        )
        self._thread.start()
        logger.info("[DepthSensor.start] 深度相机已启动 device=%s", path)

    def close(self) -> None:
        """停止读取线程；线程在下一帧到达时退出并关闭设备."""
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._stopping.set()
        thread.join(timeout=2.0)
        with self._lock:
            self._latest = None

    def latest(self) -> Clearance | None:
        """返回最近一次计算的空隙；读取线程异常退出后不再更新."""
        with self._lock:
            return self._latest

    def _run(self, device: _Device) -> None:
        """读帧循环；异常只记日志，空隙停止更新，由守护按过期处理."""
        last = 0.0
        try:
            for frame in device:
                if self._stopping.is_set():
                    break
                now = time.time()
                if now - last < _UPDATE_INTERVAL_S:
                    continue
                last = now
                result = measure_clearance(
                    frame_to_depth(bytes(frame)), now, self._geometry
                )
                with self._lock:
                    self._latest = result
        except Exception:
            logger.exception("[DepthSensor._run] 深度读取中断")
        finally:
            device.close()
