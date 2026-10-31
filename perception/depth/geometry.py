# perception/depth/geometry.py
"""深度图到车前空隙的换算：去掉地面与高过车身的点，按方向给出最近障碍距离."""

import math
from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

from domain.validation import finite_float

# D435 深度 640×480 的典型内参；水平视场约 ±43°。
_DEFAULT_FX = 383.0
_DEFAULT_FY = 383.0
_DEFAULT_CX = 320.0
_DEFAULT_CY = 240.0
# 深度相机近处读不出距离，远处噪声大；超出该范围的读数不参与计算。
_MIN_RANGE_M = 0.2
_MAX_RANGE_M = 4.0
# 每隔若干像素取样，降低 Jetson CPU 占用；640×480 取样后约 160×120。
_STRIDE = 4
# 方向扇区：视场内 5 个候选方向，左为正，弧度。
HEADINGS_RAD: tuple[float, ...] = tuple(
    math.radians(value) for value in (34.8, 17.4, 0.0, -17.4, -34.8)
)


@dataclass(frozen=True)
class DepthGeometry:
    """摄像头安装、内参与车身尺寸；距离单位为米，俯仰朝上为正（弧度）."""

    camera_height_m: float
    camera_pitch_rad: float
    body_height_m: float = 0.3
    body_width_m: float = 0.25
    side_margin_m: float = 0.075
    floor_margin_m: float = 0.03
    min_points: int = 50
    free_range_m: float = 4.0
    fx: float = _DEFAULT_FX
    fy: float = _DEFAULT_FY
    cx: float = _DEFAULT_CX
    cy: float = _DEFAULT_CY

    def __post_init__(self) -> None:
        """校验尺寸为正、俯仰在 ±60° 内."""
        for name in (
            "camera_height_m",
            "body_height_m",
            "body_width_m",
            "free_range_m",
            "fx",
            "fy",
        ):
            if finite_float(cast(object, getattr(self, name)), name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("side_margin_m", "floor_margin_m", "cx", "cy"):
            if finite_float(cast(object, getattr(self, name)), name) < 0:
                raise ValueError(f"{name} must not be negative")
        if abs(finite_float(self.camera_pitch_rad, "camera_pitch_rad")) > math.radians(
            60
        ):
            raise ValueError("camera_pitch_rad must be within ±60°")
        if self.min_points <= 0:
            raise ValueError("min_points must be positive")

    @property
    def corridor_half_width_m(self) -> float:
        """车身通道半宽：车宽一半加单侧余量."""
        return self.body_width_m / 2 + self.side_margin_m


@dataclass(frozen=True)
class Clearance:
    """一帧深度换算出的空隙；距离从摄像头算起，没有障碍点时为 free_range_m."""

    captured_at: float
    ahead_m: float
    headings: tuple[tuple[float, float], ...]
    # 画面中部有效深度像素比例；贴近障碍时读数会骤减，供守护判断
    center_valid_ratio: float


type FloatArray = NDArray[np.float64]


def _sample(
    depth_mm: NDArray[np.uint16], geometry: DepthGeometry, *, lower_half: bool = False
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """取样有效读数，返回相机坐标 x（右）、y（下）、z（前），单位米."""
    sampled: FloatArray = depth_mm[::_STRIDE, ::_STRIDE].astype(np.float64) / 1000.0
    height, width = cast(tuple[int, int], sampled.shape)
    cols: FloatArray = np.broadcast_to(
        np.arange(width, dtype=np.float64) * _STRIDE, (height, width)
    )
    rows: FloatArray = np.broadcast_to(
        (np.arange(height, dtype=np.float64) * _STRIDE)[:, np.newaxis], (height, width)
    )
    valid = (sampled >= _MIN_RANGE_M) & (sampled <= _MAX_RANGE_M)
    if lower_half:
        valid &= rows > geometry.cy
    z: FloatArray = sampled[valid]
    x: FloatArray = (cols[valid] - geometry.cx) * z / geometry.fx
    y: FloatArray = (rows[valid] - geometry.cy) * z / geometry.fy
    return x, y, z


def body_points(
    depth_mm: NDArray[np.uint16], geometry: DepthGeometry
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """把深度图取样换算到车体坐标（前、左、离地高度），只返回有效读数.

    Args:
        depth_mm: 毫米单位的深度图，0 表示无读数。
        geometry: 安装与内参。

    Returns:
        三个等长数组：前向距离、向左偏移、离地高度，单位米。
    """
    x, y, z = _sample(depth_mm, geometry)
    # 摄像头朝上俯仰 θ：光轴在车体中为 (cosθ 前, sinθ 上)，图像向下轴为 (sinθ 前, -cosθ 上)
    sin_p = math.sin(geometry.camera_pitch_rad)
    cos_p = math.cos(geometry.camera_pitch_rad)
    forward: FloatArray = z * cos_p + y * sin_p
    up: FloatArray = geometry.camera_height_m + z * sin_p - y * cos_p
    return forward, -x, up


def measure_clearance(
    depth_mm: NDArray[np.uint16], captured_at: float, geometry: DepthGeometry
) -> Clearance:
    """计算正前方通道及各候选方向的空隙.

    只把离地高度在 floor_margin_m 与 body_height_m 之间的点当作障碍；
    每个方向取通道内障碍点沿该方向距离的第 5 百分位，点数不足视为无障碍。

    Args:
        depth_mm: 毫米单位的深度图。
        captured_at: 采集时间（秒）。
        geometry: 安装、内参与车身尺寸。

    Returns:
        空隙结果。
    """
    forward, left, up = body_points(depth_mm, geometry)
    obstacle = (up >= geometry.floor_margin_m) & (up <= geometry.body_height_m)
    forward = forward[obstacle]
    left = left[obstacle]
    headings = tuple(
        (angle, _corridor_clearance(forward, left, angle, geometry))
        for angle in HEADINGS_RAD
    )
    ahead = _corridor_clearance(forward, left, 0.0, geometry)
    height, width = cast(tuple[int, int], depth_mm.shape)
    center = depth_mm[height // 3 : 2 * height // 3, width // 3 : 2 * width // 3]
    ratio = float(np.count_nonzero(center)) / max(1, center.size)
    return Clearance(captured_at, ahead, headings, ratio)


def _corridor_clearance(
    forward: NDArray[np.float64],
    left: NDArray[np.float64],
    angle: float,
    geometry: DepthGeometry,
) -> float:
    """沿 angle 方向、宽为车身通道的空隙."""
    along: FloatArray = forward * math.cos(angle) + left * math.sin(angle)
    across: FloatArray = -forward * math.sin(angle) + left * math.cos(angle)
    inside = (along > 0) & (np.abs(across) <= geometry.corridor_half_width_m)
    distances: FloatArray = along[inside]
    if distances.size < geometry.min_points:
        return geometry.free_range_m
    return min(geometry.free_range_m, float(np.percentile(distances, 5)))


@dataclass(frozen=True)
class MountEstimate:
    """空地上拟合出的摄像头离地高度、俯仰与车前盲区."""

    camera_height_m: float
    camera_pitch_rad: float
    # 车前最近可见地面的距离；俯仰过大使地面完全不可见时为 inf
    blind_distance_m: float
    inliers: int


def estimate_mount(
    depth_mm: NDArray[np.uint16],
    geometry: DepthGeometry,
    *,
    iterations: int = 300,
    tolerance_m: float = 0.015,
    seed: int = 0,
) -> MountEstimate:
    """用 RANSAC 拟合地面平面，求摄像头离地高度与俯仰角.

    Args:
        depth_mm: 车放在空地上采集的深度图。
        geometry: 只使用其中的内参。
        iterations: 采样次数。
        tolerance_m: 点到平面距离阈值。
        seed: 随机种子，保证结果可复现。

    Returns:
        拟合结果。

    Raises:
        ValueError: 有效点不足或找不到足够大的平面。
    """
    # 地面只出现在画面下半部
    x, y, z = _sample(depth_mm, geometry, lower_half=True)
    points: FloatArray = np.stack((x, y, z), axis=1)
    if len(points) < 200:
        raise ValueError("Not enough depth points in the lower half of the image")
    rng = np.random.default_rng(seed)
    best_count = 0
    best_normal: tuple[float, float, float] = (0.0, 0.0, 0.0)
    best_offset = 0.0
    for _ in range(iterations):
        picked: FloatArray = points[rng.choice(len(points), 3, replace=False)]
        (ax, ay, az), (bx, by, bz), (qx, qy, qz) = cast(
            list[list[float]], picked.tolist()
        )
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = qx - ax, qy - ay, qz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        norm = math.sqrt(nx * nx + ny * ny + nz * nz)
        if norm < 1e-9:
            continue
        nx, ny, nz = nx / norm, ny / norm, nz / norm
        # 法向朝上：在相机坐标中“上”的 y 分量为负
        if ny > 0:
            nx, ny, nz = -nx, -ny, -nz
        offset = -(nx * ax + ny * ay + nz * az)
        distance: FloatArray = np.abs(points @ np.array((nx, ny, nz)) + offset)
        count = int(np.count_nonzero(distance < tolerance_m))
        if count > best_count:
            best_count, best_normal, best_offset = count, (nx, ny, nz), offset
    if best_count < max(200, len(points) // 5):
        raise ValueError("No dominant floor plane found; place the robot on open floor")
    # 车体“上”在相机坐标中为 (0, -cosθ, sinθ)
    pitch = math.atan2(best_normal[2], -best_normal[1])
    height = abs(best_offset)
    # 画面最下一行的视线相对水平向下的角度
    last_row = cast(tuple[int, int], depth_mm.shape)[0] - 1
    bottom = math.atan((last_row - geometry.cy) / geometry.fy) - pitch
    blind = height / math.tan(bottom) if bottom > 0 else math.inf
    return MountEstimate(height, pitch, blind, best_count)
