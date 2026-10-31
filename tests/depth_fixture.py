# tests/depth_fixture.py
"""按摄像头安装姿态渲染合成深度图：地面加若干长方体障碍，供深度相关测试使用."""

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from perception.depth.geometry import DepthGeometry

type FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class Box:
    """车体坐标中的长方体：前向、向左、离地高度的区间，单位米."""

    forward: tuple[float, float]
    left: tuple[float, float]
    up: tuple[float, float] = (0.0, 0.5)


def render(
    geometry: DepthGeometry,
    boxes: tuple[Box, ...] = (),
    *,
    floor: bool = True,
    shape: tuple[int, int] = (480, 640),
) -> NDArray[np.uint16]:
    """逐像素求视线与地面、长方体的最近交点，返回毫米深度，无交点为 0."""
    height, width = shape
    cols: FloatArray = np.broadcast_to(np.arange(width, dtype=np.float64), shape)
    rows: FloatArray = np.broadcast_to(
        np.arange(height, dtype=np.float64)[:, np.newaxis], shape
    )
    rx: FloatArray = (cols - geometry.cx) / geometry.fx
    ry: FloatArray = (rows - geometry.cy) / geometry.fy
    sin_p = math.sin(geometry.camera_pitch_rad)
    cos_p = math.cos(geometry.camera_pitch_rad)
    # 视线方向（相机 z=1）换到车体坐标：前、左、上
    d_forward: FloatArray = cos_p + sin_p * ry
    d_left: FloatArray = -rx
    d_up: FloatArray = sin_p - cos_p * ry
    best: FloatArray = np.full(shape, np.inf)
    if floor:
        with np.errstate(divide="ignore", invalid="ignore"):
            t_floor: FloatArray = -geometry.camera_height_m / d_up
        best = np.where((d_up < 0) & (t_floor > 0), t_floor, best)
    origin = (0.0, 0.0, geometry.camera_height_m)
    for box in boxes:
        t_near: FloatArray = np.full(shape, -np.inf)
        t_far: FloatArray = np.full(shape, np.inf)
        for start, direction, (low, high) in zip(
            origin,
            (d_forward, d_left, d_up),
            (box.forward, box.left, box.up),
            strict=True,
        ):
            with np.errstate(divide="ignore", invalid="ignore"):
                t1: FloatArray = (low - start) / direction
                t2: FloatArray = (high - start) / direction
            parallel: NDArray[np.bool_] = np.equal(direction, 0.0)
            inside = (start >= low) & (start <= high)
            t1 = np.where(parallel, np.where(inside, -np.inf, np.inf), t1)
            t2 = np.where(parallel, np.where(inside, np.inf, -np.inf), t2)
            t_near = np.maximum(t_near, np.minimum(t1, t2))
            t_far = np.minimum(t_far, np.maximum(t1, t2))
        hit = (t_near <= t_far) & (t_near > 0)
        best = np.where(hit & (t_near < best), t_near, best)
    # 相机 z=1 的视线参数 t 即深度
    depth: FloatArray = np.where(np.isfinite(best), best * 1000.0, 0.0)
    return np.clip(np.round(depth), 0, 65535).astype(np.uint16)
