# skills/turn_relative.py
"""原地相对转向 Skill，执行与位置验证通过统一适配器完成."""

from typing import override

from domain.models import SkillResult
from domain.robot import RobotCapability
from domain.validation import finite_float, string_key_dict
from robot.base import RobotAdapter
from skills.move_relative import MoveRelativeSkill


class TurnRelativeSkill(MoveRelativeSkill):
    """复用平面位姿验证与停止清理，转向单位为弧度."""

    name: str = "turn_relative"
    parameter_help: str = (
        'arguments={"angle_rad": 数值, "speed_rad_s": 可选数值}；'
        + "角度弧度，逆时针为正，范围 [-π,π]；角速度 (0,1]，默认 0.3。"
    )
    required_capabilities: frozenset[RobotCapability] = frozenset(
        {
            RobotCapability.TURN_RELATIVE,
            RobotCapability.LOCALIZATION,
        }
    )

    @override
    def _parse_arguments(self, args: object) -> tuple[float, float]:
        """限制单次转角及角速度，拒绝未知字段和布尔值."""
        values = string_key_dict(args, "arguments")
        if values.keys() - {"angle_rad", "speed_rad_s"}:
            raise ValueError("Unknown turn arguments")
        angle = finite_float(values.get("angle_rad"), "angle_rad")
        speed = finite_float(values.get("speed_rad_s", 0.3), "speed_rad_s")
        if abs(angle) > 3.141592653589793 or not 0 < speed <= 1:
            raise ValueError("Turn exceeds angle or speed limit")
        return angle, speed

    @override
    async def execute(
        self, robot: RobotAdapter, args: dict[str, object]
    ) -> SkillResult:
        """检查能力，执行转向并保存目标位姿作为完成验证依据."""
        angle, speed = self._parse_arguments(args)
        await self.check_preconditions(robot)
        origin = (await robot.get_state()).position
        await robot.turn_relative(angle, speed)
        return SkillResult(
            f"相对转向：请求角度 {angle:g} 弧度",
            {
                "simulated": robot.is_simulated,
                "expected_position": {
                    "x": origin.x,
                    "y": origin.y,
                    "yaw": origin.yaw + angle,
                },
            },
        )
