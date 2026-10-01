# app/demo.py
"""模拟演示入口：装配依赖、提交任务，并打印 Runtime 推送的状态."""

import argparse
import asyncio
import logging
import os

from domain.models import ActionStatus
from robot.simulated import SimulatedAdapter, SimulationMode
from runtime.action_manager import ActionManager
from runtime.task_coordinator import TaskCoordinator
from skills.move_relative import MoveRelativeSkill
from skills.registry import SkillRegistry

_SCENARIOS = ("success", "failure", "timeout", "cancel", "queued_cancel", "sequence")


class DemoArguments(argparse.Namespace):
    """命令行演示参数."""

    scenario: str = "success"


async def print_events(
    runtime: ActionManager, action_count: int, started: asyncio.Event
) -> None:
    """消费动作状态事件，展示进度并通知动作已开始."""
    finished = 0
    while finished < action_count:
        event = await runtime.next_event()
        print(
            f"  [事件] task={event.task_id} action={event.action_id} {event.status.value}"
        )
        if event.status == ActionStatus.RUNNING:
            started.set()
        if event.status.is_terminal:
            finished += 1


async def run_scenario(scenario: str) -> None:
    """装配模拟设备和运行时，执行指定演示场景."""
    mode = {
        "failure": SimulationMode.FAILURE,
        "timeout": SimulationMode.HANG,
        "cancel": SimulationMode.HANG,
    }.get(scenario, SimulationMode.SUCCESS)
    robot = SimulatedAdapter(_mode=mode, _time_scale=0.1)
    registry = SkillRegistry([MoveRelativeSkill()])
    print(f"\n=== {scenario}（模拟设备，相对移动）===")

    async with ActionManager(robot, registry) as runtime:
        coordinator = TaskCoordinator(runtime)
        task = coordinator.create_task(f"演示 {scenario}")
        first = await coordinator.submit_action(
            task.task_id,
            "move_relative",
            {"distance_m": 0.3, "speed_m_s": 0.1},
            timeout_s=0.1 if scenario == "timeout" else 3.0,
        )
        action_ids = [first.action_id]
        print(f"  已接受动作 {first.action_id}，提交返回状态：{first.status.value}")
        if scenario in {"queued_cancel", "sequence"}:
            second = await coordinator.submit_action(
                task.task_id,
                "move_relative",
                {"distance_m": -0.1, "speed_m_s": 0.1},
            )
            action_ids.append(second.action_id)
            if scenario == "queued_cancel":
                _ = await runtime.cancel_action(second.action_id)

        started = asyncio.Event()
        printer = asyncio.create_task(print_events(runtime, len(action_ids), started))
        try:
            if scenario == "cancel":
                _ = await started.wait()
                await asyncio.sleep(0.02)
                task = await coordinator.cancel_task(task.task_id)
            records = await asyncio.gather(
                *(runtime.wait_for_action(action_id) for action_id in action_ids)
            )
            await printer
            if scenario != "cancel":
                task = coordinator.complete_task(task.task_id)
            for record in records:
                print(f"  动作 {record.action_id} 最终状态：{record.status.value}")
                if record.result is not None:
                    print(f"  结果：{record.result.summary}")
                    print(f"  证据：{record.result.evidence}")
                if record.failure is not None:
                    print(f"  原因：{record.failure}")
            state = await robot.get_state()
            print(f"  任务状态：{task.status.value}")
            print(f"  设备运动中：{state.is_moving}；最终位姿：{state.position}")
        finally:
            _ = printer.cancel()
            _ = await asyncio.gather(printer, return_exceptions=True)


def run_app() -> None:
    """解析命令行参数并启动异步演示."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="教学机器人基础层：纯模拟动作演示")
    _ = parser.add_argument(
        "--scenario", choices=(*_SCENARIOS, "all"), default="success"
    )
    args = parser.parse_args(namespace=DemoArguments())

    async def run() -> None:
        """按选定场景顺序运行演示."""
        for scenario in _SCENARIOS if args.scenario == "all" else (args.scenario,):
            await run_scenario(scenario)

    asyncio.run(run())
