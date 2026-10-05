# app/robot_demo.py
"""无模型的设备底座演示：前进、转向、前进，展示实际执行证据."""

import asyncio
import os

from robot.factory import RobotAdapterFactory
from runtime.action_manager import ActionManager
from runtime.task_coordinator import TaskCoordinator
from skills.robot_registry import build_robot_skills


async def run_app() -> None:
    """通过适配器工厂装配设备，用 Runtime 执行运动序列."""
    robot = RobotAdapterFactory().create(os.environ.get("ROBOT_ADAPTER", "simulated"))
    if not robot.is_simulated:
        raise ValueError("This scripted demo only permits simulated devices")
    async with ActionManager(robot, build_robot_skills(robot)) as runtime:
        coordinator = TaskCoordinator(runtime)
        task = coordinator.create_task("模拟设备运动验收")
        print("模拟设备，能力：", sorted(robot.capabilities))
        for skill, args in (
            ("move_relative", {"distance_m": 0.2}),
            ("turn_relative", {"angle_rad": 1.5707963267948966}),
            ("move_relative", {"distance_m": 0.2}),
        ):
            action = await coordinator.submit_action(
                task.task_id, skill, dict[str, object](args)
            )
            result = await runtime.wait_for_action(action.action_id)
            print(skill, result.status.value, (await robot.get_state()).position)
            if result.failure is not None:
                _ = await coordinator.cancel_task(task.task_id)
                raise RuntimeError(result.failure)
        print("任务状态：", coordinator.complete_task(task.task_id).status.value)


if __name__ == "__main__":
    asyncio.run(run_app())
