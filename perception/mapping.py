# perception/mapping.py
"""平面栅格地图：深度扫描按里程计位姿画进网格，供显示与绕障回程规划.

坐标系为里程计坐标（米），原点是底盘连接时的位置。没有回环校正，
里程计漂移会让地图随时间变歪；适合房间内几分钟、几米范围的陪伴。
"""

import asyncio
import heapq
import logging
import math
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

from domain.models import Pose2D, RobotState
from perception.depth.camera import ClearanceSource

logger = logging.getLogger(__name__)

UNKNOWN = 0
FREE = 1
OCCUPIED = 2

# 对数几率累积：看见空地减、看见障碍加，饱和避免一次误读改不回来
_HIT = 3
_MISS = -1
_LIMIT = 12
_OCCUPIED_AT = 3
_FREE_AT = -2
# 看不到障碍时只把这么远以内记为空地：远处深度噪声大
_FREE_REACH_M = 2.5


@dataclass(frozen=True)
class MapView:
    """地图快照：cells 为按行（y 由小到大）排列的 UNKNOWN/FREE/OCCUPIED."""

    resolution_m: float
    origin_x: float
    origin_y: float
    width: int
    height: int
    cells: bytes


class OccupancyMap:
    """线程安全的栅格地图；深度线程写入，事件循环读取."""

    def __init__(self, _resolution_m: float = 0.05, _size_m: float = 16.0) -> None:
        """地图以里程计原点为中心.

        Args:
            _resolution_m: 每格边长（米）。
            _size_m: 地图边长（米）。

        Raises:
            ValueError: 参数不为正。
        """
        if _resolution_m <= 0 or _size_m <= _resolution_m:
            raise ValueError("map resolution and size must be positive")
        self._resolution: float = _resolution_m
        self._cells: int = int(round(_size_m / _resolution_m))
        self._origin: float = -self._cells * _resolution_m / 2
        self._odds: NDArray[np.int8] = np.zeros((self._cells, self._cells), np.int8)
        self._lock: threading.Lock = threading.Lock()

    @property
    def resolution_m(self) -> float:
        """每格边长."""
        return self._resolution

    def cell(self, x: float, y: float) -> tuple[int, int] | None:
        """坐标所在格（行，列）；超出地图返回 None."""
        col = int(math.floor((x - self._origin) / self._resolution))
        row = int(math.floor((y - self._origin) / self._resolution))
        if 0 <= row < self._cells and 0 <= col < self._cells:
            return row, col
        return None

    def center(self, row: int, col: int) -> tuple[float, float]:
        """格中心坐标."""
        return (
            self._origin + (col + 0.5) * self._resolution,
            self._origin + (row + 0.5) * self._resolution,
        )

    def integrate(
        self, pose: Pose2D, scan: tuple[tuple[float, float, bool], ...]
    ) -> None:
        """把一次扫描画进地图：射线经过的格记空地，碰到障碍的终点格记障碍."""
        step = self._resolution / 2
        misses: set[tuple[int, int]] = set()
        hits: set[tuple[int, int]] = set()
        for bearing, reach, blocked in scan:
            angle = pose.yaw + bearing
            length = reach if blocked else min(reach, _FREE_REACH_M)
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            distance = 0.0
            while distance < length - step:
                cell = self.cell(pose.x + distance * cos_a, pose.y + distance * sin_a)
                if cell is not None:
                    misses.add(cell)
                distance += step
            if blocked:
                cell = self.cell(pose.x + reach * cos_a, pose.y + reach * sin_a)
                if cell is not None:
                    hits.add(cell)
        misses -= hits
        with self._lock:
            for cells, delta in ((misses, _MISS), (hits, _HIT)):
                if not cells:
                    continue
                rows = np.array([row for row, _ in cells], np.intp)
                cols = np.array([col for _, col in cells], np.intp)
                values = self._odds[rows, cols].astype(np.int16) + delta
                self._odds[rows, cols] = np.clip(values, -_LIMIT, _LIMIT).astype(
                    np.int8
                )

    def _states(self) -> NDArray[np.uint8]:
        """当前每格状态."""
        with self._lock:
            odds = self._odds.copy()
        states = np.full(odds.shape, UNKNOWN, np.uint8)
        states[odds <= _FREE_AT] = FREE
        states[odds >= _OCCUPIED_AT] = OCCUPIED
        return states

    def view(self, margin_cells: int = 10) -> MapView:
        """裁到已知区域（加边距）的快照；全未知时返回原点附近的小块."""
        states = self._states()
        rows, cols = np.nonzero(states)
        if rows.size == 0:
            middle = self._cells // 2
            row0 = col0 = max(0, middle - margin_cells)
            row1 = col1 = min(self._cells, middle + margin_cells)
        else:
            row0 = max(0, int(rows.min()) - margin_cells)
            row1 = min(self._cells, int(rows.max()) + 1 + margin_cells)
            col0 = max(0, int(cols.min()) - margin_cells)
            col1 = min(self._cells, int(cols.max()) + 1 + margin_cells)
        crop = np.ascontiguousarray(states[row0:row1, col0:col1])
        return MapView(
            self._resolution,
            self._origin + col0 * self._resolution,
            self._origin + row0 * self._resolution,
            col1 - col0,
            row1 - row0,
            crop.tobytes(),
        )

    def plan(
        self, start: Pose2D, goal: Pose2D, clearance_m: float
    ) -> list[tuple[float, float]] | None:
        """在已知障碍外（膨胀 clearance_m）找一条路，返回拐点坐标，不含起点.

        未知格按可通行处理，实际前进仍由避障守护把关。
        找不到路或起终点在地图外时返回 None。
        """
        begin = self.cell(start.x, start.y)
        end = self.cell(goal.x, goal.y)
        if begin is None or end is None:
            return None
        blocked = cast(NDArray[np.bool_], self._states() == OCCUPIED)
        radius = max(1, int(math.ceil(clearance_m / self._resolution)))
        inflated = _dilate(blocked, radius)
        # 起终点所在的一小片不算障碍：车本身就在那里
        for row, col in (begin, end):
            inflated[
                max(0, row - radius) : row + radius + 1,
                max(0, col - radius) : col + radius + 1,
            ] = False
        path = _astar(inflated, begin, end)
        if path is None:
            return None
        corners = _shortcut(inflated, path)
        return [self.center(row, col) for row, col in corners[1:]]


def _dilate(mask: NDArray[np.bool_], radius: int) -> NDArray[np.bool_]:
    """按圆形半径膨胀障碍."""
    result = mask.copy()
    rows, cols = cast(tuple[int, int], mask.shape)
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            if dr * dr + dc * dc > radius * radius or (dr == 0 and dc == 0):
                continue
            shifted = np.zeros_like(mask)
            shifted[max(0, dr) : rows + min(0, dr), max(0, dc) : cols + min(0, dc)] = (
                mask[max(0, -dr) : rows + min(0, -dr), max(0, -dc) : cols + min(0, -dc)]
            )
            result |= shifted
    return result


_MOVES = (
    (1, 0, 1.0),
    (-1, 0, 1.0),
    (0, 1, 1.0),
    (0, -1, 1.0),
    (1, 1, math.sqrt(2)),
    (1, -1, math.sqrt(2)),
    (-1, 1, math.sqrt(2)),
    (-1, -1, math.sqrt(2)),
)


def _astar(
    blocked: NDArray[np.bool_], begin: tuple[int, int], end: tuple[int, int]
) -> list[tuple[int, int]] | None:
    """八邻域 A*；返回含起终点的格序列."""
    rows, cols = cast(tuple[int, int], blocked.shape)
    best: dict[tuple[int, int], float] = {begin: 0.0}
    parent: dict[tuple[int, int], tuple[int, int]] = {}
    frontier: list[tuple[float, float, tuple[int, int]]] = [(0.0, 0.0, begin)]
    while frontier:
        _, cost, node = heapq.heappop(frontier)
        if node == end:
            path = [node]
            while node in parent:
                node = parent[node]
                path.append(node)
            return path[::-1]
        if cost > best.get(node, math.inf):
            continue
        for dr, dc, step in _MOVES:
            row, col = node[0] + dr, node[1] + dc
            if not (0 <= row < rows and 0 <= col < cols) or cast(
                bool, blocked[row, col]
            ):
                continue
            total = cost + step
            if total < best.get((row, col), math.inf):
                best[(row, col)] = total
                parent[(row, col)] = node
                guess = total + math.hypot(end[0] - row, end[1] - col)
                heapq.heappush(frontier, (guess, total, (row, col)))
    return None


def _clear_line(
    blocked: NDArray[np.bool_], first: tuple[int, int], second: tuple[int, int]
) -> bool:
    """两格之间的直线不经过障碍."""
    steps = max(abs(second[0] - first[0]), abs(second[1] - first[1])) * 2
    for index in range(steps + 1):
        ratio = index / steps if steps else 0.0
        row = int(round(first[0] + (second[0] - first[0]) * ratio))
        col = int(round(first[1] + (second[1] - first[1]) * ratio))
        if cast(bool, blocked[row, col]):
            return False
    return True


def _shortcut(
    blocked: NDArray[np.bool_], path: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """去掉直线可达的中间格，只留拐点."""
    corners = [path[0]]
    anchor = 0
    while anchor < len(path) - 1:
        reach = len(path) - 1
        while reach > anchor + 1 and not _clear_line(
            blocked, path[anchor], path[reach]
        ):
            reach -= 1
        corners.append(path[reach])
        anchor = reach
    return corners


# 停稳后再等这么久的深度帧才画图：动作刚结束的帧可能还带着运动
_SETTLE_S = 0.3
_POLL_S = 0.2


class MapBuilder:
    """后台把深度扫描按里程计位姿画进地图；只在车停稳时画，避免位姿与画面错位."""

    def __init__(
        self,
        _map: OccupancyMap,
        _depth: ClearanceSource,
        _state: Callable[[], Awaitable[RobotState]],
    ) -> None:
        """注入地图、深度来源与底盘状态读取."""
        self.map: OccupancyMap = _map
        self._depth: ClearanceSource = _depth
        self._state: Callable[[], Awaitable[RobotState]] = _state
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """启动后台画图，重复调用安全."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="map-builder")

    async def close(self) -> None:
        """停止后台画图."""
        task, self._task = self._task, None
        if task is not None:
            _ = task.cancel()
            _ = await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        """轮询深度；单次出错只记日志."""
        stopped_at: float | None = None
        drawn = 0.0
        while True:
            await asyncio.sleep(_POLL_S)
            try:
                state = await self._state()
                if state.is_moving:
                    stopped_at = None
                    continue
                if stopped_at is None:
                    stopped_at = state.updated_at
                reading = self._depth.latest()
                if (
                    reading is None
                    or not reading.scan
                    or reading.captured_at <= drawn
                    or reading.captured_at < stopped_at + _SETTLE_S
                ):
                    continue
                self.map.integrate(state.position, reading.scan)
                drawn = reading.captured_at
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[MapBuilder._run] 画图失败")
