# tests/test_mapping.py
"""实时地图：深度扫描、栅格画图、绕障规划与地图接口."""

import base64
import math
import unittest

from domain.models import Pose2D
from perception.depth.geometry import DepthGeometry, measure_clearance
from perception.mapping import FREE, OCCUPIED, UNKNOWN, OccupancyMap
from tests.depth_fixture import Box, render


def cell_state(occupancy: OccupancyMap, x: float, y: float) -> int:
    """读出坐标所在格的状态."""
    view = occupancy.view()
    col = int(math.floor((x - view.origin_x) / view.resolution_m))
    row = int(math.floor((y - view.origin_y) / view.resolution_m))
    if not (0 <= row < view.height and 0 <= col < view.width):
        return UNKNOWN
    return view.cells[row * view.width + col]


def wall(occupancy: OccupancyMap, x: float, half_width: float, times: int = 2) -> None:
    """从原点看向 x 处、宽 2×half_width 的一堵墙，画 times 次."""
    scan: list[tuple[float, float, bool]] = []
    steps = 20
    for index in range(steps + 1):
        y = -half_width + 2 * half_width * index / steps
        scan.append((math.atan2(y, x), math.hypot(x, y), True))
    for _ in range(times):
        occupancy.integrate(Pose2D(), tuple(scan))


class ScanTests(unittest.TestCase):
    """深度帧换算出按方位的扫描."""

    def test_box_ahead_hits_center_sectors_only(self) -> None:
        """正前方 1 米的箱子：中间扇区碰到约 1 米，两边扇区没有障碍."""
        geo = DepthGeometry(camera_height_m=0.2, camera_pitch_rad=math.radians(5))
        depth = render(geo, (Box((1.0, 1.3), (-0.3, 0.3), (0.0, 0.25)),))
        scan = measure_clearance(depth, 0.0, geo).scan
        self.assertEqual(len(scan), 32)
        center = [item for item in scan if abs(item[0]) < 0.1]
        self.assertTrue(center)
        for _, reach, hit in center:
            self.assertTrue(hit)
            self.assertAlmostEqual(reach, 1.0, delta=0.05)
        for bearing, reach, hit in scan:
            if abs(bearing) > 0.5:
                self.assertFalse(hit)
                self.assertEqual(reach, geo.free_range_m)


class OccupancyMapTests(unittest.TestCase):
    """画图与规划."""

    def test_hit_is_occupied_and_ray_is_free(self) -> None:
        """障碍格记障碍，射线经过的格记空地，没看过的格未知."""
        occupancy = OccupancyMap()
        for _ in range(2):
            occupancy.integrate(Pose2D(), ((0.0, 1.0, True),))
        self.assertEqual(cell_state(occupancy, 1.0, 0.0), OCCUPIED)
        self.assertEqual(cell_state(occupancy, 0.5, 0.0), FREE)
        self.assertEqual(cell_state(occupancy, -0.5, 0.0), UNKNOWN)

    def test_pose_rotates_scan(self) -> None:
        """车朝左（yaw=90°）时正前方的障碍画在 +y 方向."""
        occupancy = OccupancyMap()
        for _ in range(2):
            occupancy.integrate(Pose2D(0.0, 0.0, math.pi / 2), ((0.0, 1.0, True),))
        self.assertEqual(cell_state(occupancy, 0.0, 1.0), OCCUPIED)

    def test_free_reading_does_not_clear_far_cells(self) -> None:
        """没碰到障碍的射线只把近处记为空地."""
        occupancy = OccupancyMap()
        for _ in range(2):
            occupancy.integrate(Pose2D(), ((0.0, 4.0, False),))
        self.assertEqual(cell_state(occupancy, 1.0, 0.0), FREE)
        self.assertEqual(cell_state(occupancy, 3.5, 0.0), UNKNOWN)

    def test_plan_goes_around_wall(self) -> None:
        """出发点在墙后：路径拐点绕开墙及其膨胀区."""
        occupancy = OccupancyMap()
        wall(occupancy, 1.0, 0.5)
        path = occupancy.plan(Pose2D(), Pose2D(2.0, 0.0), 0.2)
        assert path is not None
        self.assertGreater(len(path), 1)
        self.assertAlmostEqual(path[-1][0], 2.0, delta=0.05)
        self.assertTrue(any(abs(y) > 0.6 for _, y in path))
        # 相邻拐点之间的直线不穿过墙
        points = [(0.0, 0.0), *path]
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
            for index in range(51):
                ratio = index / 50
                x = x0 + (x1 - x0) * ratio
                y = y0 + (y1 - y0) * ratio
                self.assertFalse(abs(x - 1.0) < 0.05 and abs(y) < 0.5)

    def test_straight_when_nothing_known(self) -> None:
        """地图全空：直接一段到终点."""
        path = OccupancyMap().plan(Pose2D(), Pose2D(1.0, 1.0), 0.2)
        assert path is not None
        self.assertEqual(len(path), 1)

    def test_view_cells_match_size(self) -> None:
        """快照字节数等于宽乘高."""
        occupancy = OccupancyMap()
        wall(occupancy, 1.0, 0.5)
        view = occupancy.view()
        self.assertEqual(len(view.cells), view.width * view.height)
        self.assertEqual(
            len(base64.b64decode(base64.b64encode(view.cells))), len(view.cells)
        )
