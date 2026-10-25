# tests/test_car_adapter.py
"""麦克纳姆底盘适配器：经固件替身验证里程计、运动到位、停止确认与故障处理."""

import asyncio
import math
import os
import unittest
from unittest.mock import patch

from app.car_calibrate import axis_counts
from domain.models import ActionRequest, ActionStatus
from robot.car_adapter import CarAdapter, CarConfig, car_config_from_env
from robot.factory import RobotAdapterFactory
from runtime.action_manager import ActionManager
from skills.robot_registry import build_robot_skills
from tests.car_firmware_fixture import FirmwareSerial

# 标定常数只需与替身一致：满量程 1000 千分比 = 4000 计数/秒 = 1 米/秒。
_COUNTS_PER_METER = 4000.0
_COUNTS_PER_RADIAN = 600.0


def _adapter(port: FirmwareSerial, *, calibrated: bool = True) -> CarAdapter:
    """用替身串口构造适配器."""
    config = (
        CarConfig("/dev/fake", _COUNTS_PER_METER, _COUNTS_PER_RADIAN)
        if calibrated
        else CarConfig("/dev/fake")
    )
    return CarAdapter(config, _open_port=lambda _path: port)


class CarAdapterTests(unittest.IsolatedAsyncioTestCase):
    """真实时间驱动替身，单项耗时约 1–3 秒."""

    async def test_connect_locks_port_and_reports_fresh_state(self) -> None:
        """连接发送停止帧锁定端口，收到遥测后报告已连接、静止、位于原点."""
        port = FirmwareSerial()
        car = _adapter(port)
        await car.connect()
        try:
            state = await car.get_state()
            car.validate_state(state)
            self.assertTrue(state.is_connected)
            self.assertFalse(state.is_moving)
            self.assertEqual(port.commands[0], "S")
            self.assertEqual(
                {item.value for item in car.capabilities},
                {"localization", "move_relative", "turn_relative"},
            )
        finally:
            await car.disconnect()
        self.assertTrue(port.closed)
        self.assertFalse((await car.get_state()).is_connected)

    async def test_uncalibrated_car_exposes_no_motion_skills(self) -> None:
        """未标定不声明能力，Agent 拿不到运动技能，直接调用也拒绝."""
        car = _adapter(FirmwareSerial(), calibrated=False)
        self.assertEqual(car.capabilities, frozenset())
        self.assertEqual(build_robot_skills(car).names(), [])
        await car.connect()
        try:
            with self.assertRaisesRegex(RuntimeError, "not calibrated"):
                await car.move_relative(0.1, 0.1)
        finally:
            await car.disconnect()

    async def test_move_and_turn_reach_odometry_targets(self) -> None:
        """左转 90° 后前进 0.2 米，到达里程计 (0, 0.2) 并确认静止."""
        port = FirmwareSerial()
        car = _adapter(port)
        await car.connect()
        try:
            await car.turn_relative(math.pi / 2, 1.0)
            await car.move_relative(0.2, 0.3)
            state = await car.get_state()
            self.assertFalse(state.is_moving)
            self.assertAlmostEqual(
                state.position.x, 0.0, delta=car.position_tolerance_m
            )
            self.assertAlmostEqual(
                state.position.y, 0.2, delta=car.position_tolerance_m
            )
            self.assertAlmostEqual(
                state.position.yaw, math.pi / 2, delta=car.yaw_tolerance_rad
            )
            self.assertEqual(port.speeds, (0.0, 0.0, 0.0, 0.0))
            self.assertIn("V", port.commands)
        finally:
            await car.disconnect()

    async def test_runtime_runs_move_skill_to_success(self) -> None:
        """经 ActionManager 与 MoveRelativeSkill 后退 0.15 米，验证按设备容差通过."""
        car = _adapter(FirmwareSerial())
        runtime = ActionManager(car, build_robot_skills(car))
        await runtime.start()
        try:
            record = await runtime.submit_action(
                ActionRequest(
                    1, "move_relative", {"distance_m": -0.15, "speed_m_s": 0.2}
                )
            )
            result = await runtime.wait_for_action(record.action_id)
            self.assertEqual(result.status, ActionStatus.SUCCEEDED, result.failure)
            self.assertAlmostEqual(
                (await car.get_state()).position.x,
                -0.15,
                delta=car.position_tolerance_m,
            )
        finally:
            await runtime.close()

    async def test_cancel_mid_move_stops_and_confirms(self) -> None:
        """运动中取消：立即发停止，stop() 由遥测确认四轮静止."""
        port = FirmwareSerial()
        car = _adapter(port)
        await car.connect()
        try:
            move = asyncio.create_task(car.move_relative(1.0, 0.3))
            await asyncio.sleep(0.4)
            self.assertTrue((await car.get_state()).is_moving)
            _ = move.cancel()
            _ = await asyncio.gather(move, return_exceptions=True)
            await car.stop()
            state = await car.get_state()
            self.assertFalse(state.is_moving)
            self.assertLess(state.position.x, 0.5)
            self.assertEqual(port.speeds, (0.0, 0.0, 0.0, 0.0))
        finally:
            await car.disconnect()

    async def test_fault_aborts_motion_and_stop_clears_it(self) -> None:
        """固件报告堵转时运动失败；停止解除锁定后可以再次运动."""
        port = FirmwareSerial()
        car = _adapter(port)
        await car.connect()
        try:
            move = asyncio.create_task(car.move_relative(1.0, 0.3))
            await asyncio.sleep(0.3)
            port.inject_fault()
            with self.assertRaisesRegex(RuntimeError, "stall|rejected"):
                await move
            await car.stop()
            await car.move_relative(0.05, 0.2)
        finally:
            await car.disconnect()

    async def test_lost_telemetry_aborts_motion_and_reports_disconnected(self) -> None:
        """遥测中断：运动报错，状态变为未连接，停止无法确认时报错."""
        port = FirmwareSerial()
        car = _adapter(port)
        await car.connect()
        try:
            move = asyncio.create_task(car.move_relative(1.0, 0.3))
            await asyncio.sleep(0.3)
            port.silent = True
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                await move
            await asyncio.sleep(0.6)
            self.assertFalse((await car.get_state()).is_connected)
            with self.assertRaisesRegex(RuntimeError, "confirm"):
                await car.stop()
        finally:
            await car.disconnect()

    async def test_encoder_wraparound_does_not_jump_pose(self) -> None:
        """累计计数跨越 i32 上限时按差值积分，位姿不跳变."""
        car = _adapter(FirmwareSerial(_encoder_offset=(1 << 31) - 200))
        await car.connect()
        try:
            await car.move_relative(0.1, 0.3)
            self.assertAlmostEqual(
                (await car.get_state()).position.x, 0.1, delta=car.position_tolerance_m
            )
        finally:
            await car.disconnect()

    def test_config_from_env_and_factory(self) -> None:
        """缺少串口路径直接报错；工厂按环境构造未连接的底盘."""
        with patch.dict(os.environ, {"CAR_SERIAL_PORT": ""}):
            with self.assertRaises(ValueError):
                _ = car_config_from_env()
        env = {
            "CAR_SERIAL_PORT": "/dev/ttyUSB0",
            "CAR_COUNTS_PER_METER": "5200",
            "CAR_COUNTS_PER_RADIAN": "900",
        }
        with patch.dict(os.environ, env):
            car = RobotAdapterFactory().create("car")
        self.assertIsInstance(car, CarAdapter)
        self.assertIn("move_relative", build_robot_skills(car).names())
        with self.assertRaises(ValueError):
            _ = CarConfig("/dev/x", counts_per_meter=-1)
        with self.assertRaises(ValueError):
            _ = CarConfig("/dev/x", max_permille=0)

    def test_calibration_axis_counts_match_odometry(self) -> None:
        """标定工具的轮组合与里程计一致：前进、左移、逆时针各自只在本轴有计数."""
        start = (0, 0, 0, 0)
        # M1 右前、M2 右后、M3 左后、M4 左前
        self.assertEqual(
            axis_counts(start, (100, 100, 100, 100)),
            {"forward": 100, "lateral": 0, "turn": 0},
        )
        self.assertEqual(
            axis_counts(start, (100, -100, 100, -100)),
            {"forward": 0, "lateral": 100, "turn": 0},
        )
        self.assertEqual(
            axis_counts(start, (100, 100, -100, -100)),
            {"forward": 0, "lateral": 0, "turn": 100},
        )
        top, bottom = (1 << 31) - 10, -(1 << 31) + 10
        wrapped = axis_counts((top, top, top, top), (bottom, bottom, bottom, bottom))
        self.assertEqual(wrapped["forward"], 20)


if __name__ == "__main__":
    _ = unittest.main()
