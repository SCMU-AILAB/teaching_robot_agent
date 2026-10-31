# tests/test_depth_geometry.py
"""深度空隙计算与安装标定：用合成深度图验证去地面、通道判断与俯仰换算."""

import math
import unittest

import numpy as np

from perception.depth.geometry import (
    HEADINGS_RAD,
    DepthGeometry,
    estimate_mount,
    measure_clearance,
)
from tests.depth_fixture import Box, render


def geometry(pitch_deg: float = 5.0, height: float = 0.2) -> DepthGeometry:
    """车高 0.3、宽 0.25 的默认几何."""
    return DepthGeometry(
        camera_height_m=height, camera_pitch_rad=math.radians(pitch_deg)
    )


class ClearanceTests(unittest.TestCase):
    """空隙只由车身高度范围内、通道内的点决定."""

    def test_floor_alone_is_free(self) -> None:
        """只有地面时各方向都无障碍."""
        geo = geometry()
        result = measure_clearance(render(geo), 1.0, geo)
        self.assertEqual(result.ahead_m, geo.free_range_m)
        self.assertTrue(all(value == geo.free_range_m for _, value in result.headings))
        self.assertEqual(result.captured_at, 1.0)

    def test_box_ahead_gives_its_distance(self) -> None:
        """正前方 1 米的箱子：前方空隙约 1 米，俯仰朝上或朝下都一样."""
        for pitch in (-10.0, 0.0, 10.0):
            with self.subTest(pitch=pitch):
                geo = geometry(pitch)
                depth = render(geo, (Box((1.0, 1.3), (-0.3, 0.3), (0.0, 0.25)),))
                result = measure_clearance(depth, 0.0, geo)
                self.assertAlmostEqual(result.ahead_m, 1.0, delta=0.03)

    def test_box_beside_corridor_only_blocks_its_side(self) -> None:
        """通道左侧外的箱子不挡正前方，只挡偏左的方向."""
        geo = geometry()
        depth = render(geo, (Box((0.8, 1.0), (0.35, 0.9), (0.0, 0.25)),))
        result = measure_clearance(depth, 0.0, geo)
        self.assertEqual(result.ahead_m, geo.free_range_m)
        by_angle = dict(result.headings)
        self.assertLess(by_angle[HEADINGS_RAD[0]], 1.5)
        self.assertEqual(by_angle[HEADINGS_RAD[-1]], geo.free_range_m)

    def test_overhang_above_body_is_ignored(self) -> None:
        """高过车身的桌面不算障碍，车可以从下面过."""
        geo = geometry(15.0)
        depth = render(geo, (Box((0.8, 1.6), (-0.6, 0.6), (0.45, 0.5)),))
        self.assertEqual(measure_clearance(depth, 0.0, geo).ahead_m, geo.free_range_m)

    def test_few_points_are_noise(self) -> None:
        """点数不足阈值的细小物体忽略."""
        geo = geometry()
        depth = render(geo, (Box((3.0, 3.02), (-0.01, 0.01), (0.1, 0.12)),))
        self.assertEqual(measure_clearance(depth, 0.0, geo).ahead_m, geo.free_range_m)

    def test_too_close_lowers_center_valid_ratio(self) -> None:
        """贴近到读不出距离时画面中部有效读数骤减."""
        geo = geometry()
        far = measure_clearance(
            render(geo, (Box((1.0, 1.2), (-0.5, 0.5), (0.0, 0.5)),)), 0.0, geo
        )
        near = measure_clearance(
            render(geo, (Box((0.1, 0.3), (-0.5, 0.5), (0.0, 0.5)),)), 0.0, geo
        )
        self.assertGreater(far.center_valid_ratio, 0.9)
        # 渲染器给出 0.1 米处的读数，换成真实相机的无读数
        self.assertGreater(near.center_valid_ratio, 0.9)
        blank = render(geo, (Box((0.1, 0.3), (-0.5, 0.5), (0.0, 0.5)),))
        blank[blank < 200] = 0
        self.assertLess(measure_clearance(blank, 0.0, geo).center_valid_ratio, 0.5)

    def test_geometry_validation(self) -> None:
        """尺寸非正、俯仰过大均拒绝."""
        with self.assertRaises(ValueError):
            _ = DepthGeometry(camera_height_m=0.0, camera_pitch_rad=0.0)
        with self.assertRaises(ValueError):
            _ = DepthGeometry(camera_height_m=0.2, camera_pitch_rad=math.radians(70))


class MountEstimateTests(unittest.TestCase):
    """空地标定求高度、俯仰与盲区."""

    def test_recovers_height_and_pitch(self) -> None:
        """合成地面上恢复安装高度与俯仰，盲区与解析值一致."""
        geo = geometry(8.0, 0.18)
        estimate = estimate_mount(render(geo), geo)
        self.assertAlmostEqual(estimate.camera_height_m, 0.18, delta=0.01)
        self.assertAlmostEqual(math.degrees(estimate.camera_pitch_rad), 8.0, delta=1.0)
        bottom = math.atan((479 - geo.cy) / geo.fy) - math.radians(8.0)
        self.assertAlmostEqual(
            estimate.blind_distance_m, 0.18 / math.tan(bottom), delta=0.05
        )

    def test_no_floor_is_rejected(self) -> None:
        """看不到地面时报错."""
        geo = geometry()
        with self.assertRaises(ValueError):
            _ = estimate_mount(render(geo, floor=False), geo)

    def test_floor_with_obstacle_still_fits(self) -> None:
        """画面里有一个箱子时仍以地面为主平面."""
        geo = geometry(3.0, 0.22)
        depth = render(geo, (Box((1.0, 1.4), (0.2, 0.6), (0.0, 0.3)),))
        estimate = estimate_mount(depth, geo)
        self.assertAlmostEqual(estimate.camera_height_m, 0.22, delta=0.01)
        self.assertTrue(np.isfinite(estimate.blind_distance_m))


if __name__ == "__main__":
    _ = unittest.main()
