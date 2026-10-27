# tests/test_record_retention.py
"""长时间运行的记录保留：上限只清理已结束任务，未结束的任务与动作永不清理."""

import unittest

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from agent.context import RobotContextBuilder
from app.audio import build_simulated_audio
from app.robot_application import RobotApplication
from domain.models import ActionRequest, ActionStatus, TaskStatus
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter
from storage.evidence import EvidenceStore
from storage.memory import InMemoryStore


def _finish(store: InMemoryStore, task_id: int, action_ids: list[int]) -> None:
    """把动作与任务写成终态."""
    for action_id in action_ids:
        record = store.get_action(action_id)
        record.status = ActionStatus.SUCCEEDED
        store.save_action(record)
    task = store.get_task(task_id)
    task.status = TaskStatus.COMPLETED
    task.action_ids = action_ids
    store.save_task(task)


class RecordRetentionTests(unittest.TestCase):
    """InMemoryStore 达到上限后的清理顺序."""

    def test_full_store_evicts_oldest_finished_task_with_its_actions(self) -> None:
        """上限 2：第三个任务挤掉最早的已结束任务及其动作，未结束任务保留."""
        store = InMemoryStore(_max_records=2)
        first = store.create_task("一")
        action = store.create_action(ActionRequest(first.task_id, "speak"))
        _finish(store, first.task_id, [action.action_id])
        second = store.create_task("二")
        third = store.create_task("三")
        with self.assertRaises(KeyError):
            _ = store.get_task(first.task_id)
        with self.assertRaises(KeyError):
            _ = store.get_action(action.action_id)
        self.assertEqual(store.get_task(second.task_id).user_target, "二")
        self.assertEqual(store.get_task(third.task_id).user_target, "三")
        # 两个任务都未结束，不能再挤掉任何一个。
        with self.assertRaises(RuntimeError):
            _ = store.create_task("四")

    def test_full_action_log_keeps_actions_of_running_tasks(self) -> None:
        """动作满额时只清理已结束任务的终态动作."""
        store = InMemoryStore(_max_records=2)
        done = store.create_task("已结束")
        old = store.create_action(ActionRequest(done.task_id, "speak"))
        _finish(store, done.task_id, [old.action_id])
        running = store.create_task("进行中")
        kept = store.create_action(ActionRequest(running.task_id, "speak"))
        newer = store.create_action(ActionRequest(running.task_id, "speak"))
        with self.assertRaises(KeyError):
            _ = store.get_action(old.action_id)
        self.assertEqual(
            [item.action_id for item in store.list_actions()],
            [kept.action_id, newer.action_id],
        )
        with self.assertRaises(RuntimeError):
            _ = store.create_action(ActionRequest(running.task_id, "speak"))


class ContextActionTests(unittest.IsolatedAsyncioTestCase):
    """多轮对话后发给模型的本轮状态不随播报次数增长."""

    async def test_context_keeps_recent_device_actions_without_speech(self) -> None:
        """2 次播报、4 次转向后，上下文只含最近 3 次转向."""
        evidence = EvidenceStore()
        app = RobotApplication(
            SimulatedAdapter(),
            SimulatedPerception(evidence),
            FakeMessagesListChatModel(responses=[]),
            _audio=build_simulated_audio(evidence),
            _evidence=evidence,
        )
        await app.start()
        try:
            task = app.gateway.create_task("多轮对话")
            turns: list[int] = []
            steps: tuple[tuple[str, dict[str, object]], ...] = (
                ("speak", {"text": "你好。"}),
                ("turn_relative", {"angle_rad": 0.01}),
                ("turn_relative", {"angle_rad": 0.01}),
                ("speak", {"text": "再见。"}),
                ("turn_relative", {"angle_rad": 0.01}),
                ("turn_relative", {"angle_rad": 0.01}),
            )
            for skill, args in steps:
                record = await app.gateway.submit_action(task.task_id, skill, args)
                done = await app.gateway.wait_for_action(task.task_id, record.action_id)
                self.assertEqual(done.status, ActionStatus.SUCCEEDED, done.failure)
                if skill == "turn_relative":
                    turns.append(record.action_id)
            context = await RobotContextBuilder(app.gateway).build(task.task_id)
            self.assertEqual(
                [item.action_id for item in context.task.actions], turns[-3:]
            )
            self.assertEqual(
                len((await app.gateway.get_snapshot(task.task_id)).actions), 6
            )
        finally:
            await app.close()


if __name__ == "__main__":
    _ = unittest.main()
