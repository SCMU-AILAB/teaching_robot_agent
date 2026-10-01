# app/team_demo.py
"""运行队友协作接口的最小演示：python -m app.team_demo."""

import asyncio

from app.team_gateway import TeamGateway
from domain.services import ObservationRequest, TranscriptResult
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.move_relative import MoveRelativeSkill
from skills.registry import SkillRegistry
from storage.evidence import EvidenceStore


async def run_demo() -> None:
    """演示观察、证据读取、语音输入去重、动作和停止接口."""
    evidence = EvidenceStore()
    perception = SimulatedPerception(evidence)
    async with ActionManager(
        SimulatedAdapter(), SkillRegistry([MoveRelativeSkill()])
    ) as runtime:
        gateway = TeamGateway(runtime, perception)
        task = gateway.create_task("认识桌面物体")
        observation = await gateway.observe(
            ObservationRequest("demo", task.task_id, "看到了什么？")
        )
        print(observation)
        print(evidence.get(observation.frame.evidence_id))
        _ = gateway.submit_transcript(
            task.task_id, "recording-demo", TranscriptResult("这是杯子")
        )
        print(await gateway.next_input())
        action = await gateway.submit_action(
            task.task_id, "move_relative", {"distance_m": 0.1}
        )
        print(await runtime.wait_for_action(action.action_id))
        print(await gateway.get_snapshot(task.task_id))
        print(await gateway.cancel_task(task.task_id))
    await perception.close()


if __name__ == "__main__":
    asyncio.run(run_demo())
