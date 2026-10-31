# tests/test_motion_claim.py
"""拦截只答应不执行：回答声称转向或移动但本轮没有提交运动动作时，要求模型重答一次."""

import unittest
from collections.abc import Sequence
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage

from agent.embodied_agent import MOTION_CLAIM_CORRECTION
from app.robot_application import RobotApplication
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter
from storage.evidence import EvidenceStore
from tests.test_object_safety_flow import RecordingChatModel, tool_call

CLAIM = "好的，我现在往左转三十度，请稍等。"


def turn_call(angle: float) -> AIMessage:
    """构造提交转向动作的模型回复."""
    return tool_call(
        "submit_action",
        {"skill": "turn_relative", "arguments": {"angle_rad": angle}},
        "t1",
    )


class MotionClaimTests(unittest.IsolatedAsyncioTestCase):
    """经完整主应用验证纠正逻辑."""

    async def _ask(
        self, responses: Sequence[BaseMessage], question: str
    ) -> tuple[RecordingChatModel, float, str]:
        """提交一句话，返回模型、回答后的航向与回答."""
        model = RecordingChatModel(responses=list(responses))
        app = RobotApplication(
            SimulatedAdapter(), SimulatedPerception(EvidenceStore()), model
        )
        try:
            await app.start()
            task = await app.create_task("纠正")
            with patch.object(
                FakeMessagesListChatModel, "bind_tools", return_value=model
            ):
                _ = app.gateway.submit_text(task.task_id, question)
                reply = (await app.process_next()).text
            yaw = (await app.runtime.robot.get_state()).position.yaw
        finally:
            await app.close()
        return model, yaw, reply

    async def test_claim_without_action_is_corrected_then_executed(self) -> None:
        """声称在转却没提交动作：追加纠正后模型提交转向，车实际转动."""
        model, yaw, reply = await self._ask(
            [AIMessage(CLAIM), turn_call(0.52), AIMessage("左转好了。")], "往左转三十度"
        )
        self.assertEqual(reply, "左转好了。")
        second = model.seen[1]
        self.assertEqual(second[-2].text, CLAIM)
        self.assertEqual(second[-1].text, MOTION_CLAIM_CORRECTION)
        self.assertAlmostEqual(yaw, 0.52, places=3)

    async def test_repeated_claim_is_not_corrected_twice(self) -> None:
        """纠正后仍声称在动只纠正一次，不反复调用模型."""
        model, _, reply = await self._ask(
            [AIMessage(CLAIM), AIMessage("我做不到转向。")], "往左转三十度"
        )
        self.assertEqual(reply, "我做不到转向。")
        self.assertEqual(len(model.seen), 2)

    async def test_claim_after_real_motion_is_accepted(self) -> None:
        """本轮已提交运动动作，回答说已经转了不再纠正."""
        model, yaw, reply = await self._ask(
            [turn_call(-0.3), AIMessage("好的，我已经向右转了一点。")], "向右转一点"
        )
        self.assertEqual(reply, "好的，我已经向右转了一点。")
        self.assertNotIn(MOTION_CLAIM_CORRECTION, [m.text for m in model.seen[-1]])
        self.assertAlmostEqual(yaw, -0.3, places=3)

    async def test_plain_answer_is_not_corrected(self) -> None:
        """不涉及移动的回答不纠正."""
        model, _, reply = await self._ask(
            [AIMessage("椅子是给人坐的，站上去容易摔倒。")], "椅子能站吗"
        )
        self.assertEqual(reply, "椅子是给人坐的，站上去容易摔倒。")
        self.assertEqual(len(model.seen), 1)


if __name__ == "__main__":
    _ = unittest.main()
