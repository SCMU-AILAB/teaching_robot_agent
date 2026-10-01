# skills/move_relative.py
"""相对移动动作的参数校验、执行与到位验证."""

import math
from typing import override

from domain.models import SkillResult
from domain.validation import finite_float, string_key_dict
from robot.base import RobotAdapter
from skills.base import RobotSkill


class MoveRelativeSkill(RobotSkill):
    """沿当前朝向移动一段距离，负距离代表后退；不提供目标点导航."""

    name: str = "move_relative"

    @override
    def validate(self, args: dict[str, object]) -> None:
        """检查动作参数，非法输入抛出异常."""
        _ = self._parse_arguments(args)

    def _parse_arguments(self, args: object) -> tuple[float, float]:
        """校验移动参数并返回距离与速度，单位为米和米每秒."""
        arguments = string_key_dict(args, "arguments")
        unknown = arguments.keys() - {"distance_m", "speed_m_s"}
        if unknown:
            raise ValueError(f"unknown arguments: {', '.join(sorted(unknown))}")
        if "distance_m" not in arguments:
            raise ValueError("distance_m is required")
        distance = finite_float(arguments["distance_m"], "distance_m")
        speed = finite_float(arguments.get("speed_m_s", 0.1), "speed_m_s")
        if abs(distance) > 2.0:
            raise ValueError("distance_m must be between -2 and 2 meters")
        if not 0 < speed <= 0.5:
            raise ValueError("speed_m_s must be greater than 0 and at most 0.5")
        return distance, speed

    @override
    async def execute(
        self, robot: RobotAdapter, args: dict[str, object]
    ) -> SkillResult:
        """执行动作并收集证据，完成判定由验证阶段负责."""
        distance, speed = self._parse_arguments(args)
        await self.check_preconditions(robot)
        before = await robot.get_state()
        start = before.position
        expected = {
            "x": start.x + distance * math.cos(start.yaw),
            "y": start.y + distance * math.sin(start.yaw),
            "yaw": start.yaw,
        }
        await robot.move_relative(distance, speed)
        after = await robot.get_state()
        return SkillResult(
            summary=f"相对移动：请求距离 {distance:g} 米",
            evidence={
                "simulated": robot.is_simulated,
                "distance_m": distance,
                "speed_m_s": speed,
                "start_position": {"x": start.x, "y": start.y, "yaw": start.yaw},
                "expected_position": expected,
                "observed_position": {
                    "x": after.position.x,
                    "y": after.position.y,
                    "yaw": after.position.yaw,
                },
                "observed_at": after.updated_at,
                "is_moving": after.is_moving,
            },
        )

    @override
    async def verify(self, robot: RobotAdapter, result: SkillResult) -> bool:
        """结合设备反馈和结果证据判断动作是否完成."""
        state = await robot.get_state()
        if not state.is_connected or state.is_moving:
            return False
        try:
            expected = string_key_dict(
                result.evidence.get("expected_position"), "expected_position"
            )
            target_x = finite_float(expected.get("x"), "expected_position.x")
            target_y = finite_float(expected.get("y"), "expected_position.y")
            target_yaw = finite_float(expected.get("yaw"), "expected_position.yaw")
        except (TypeError, ValueError):
            return False
        return all(
            math.isclose(actual, target, rel_tol=0.0, abs_tol=0.001)
            for actual, target in (
                (state.position.x, target_x),
                (state.position.y, target_y),
                (state.position.yaw, target_yaw),
            )
        )
