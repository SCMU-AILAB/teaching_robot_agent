"""Exercise observable runtime behavior without hardware or external services."""

import asyncio
import unittest
from dataclasses import replace
from typing import cast, overload, override

from domain.models import (
    ActionEvent,
    ActionRecord,
    ActionRequest,
    ActionStatus,
    RobotState,
    TaskStatus,
)
from robot.simulated import SimulatedAdapter, SimulationMode
from runtime.action_manager import ActionManager
from runtime.task_coordinator import TaskCoordinator
from skills.move_relative import MoveRelativeSkill
from skills.registry import SkillRegistry
from storage.memory import InMemoryStore


class UnstoppableAdapter(SimulatedAdapter):
    """Model a device that cannot acknowledge stopping after a fault."""

    def __init__(self) -> None:
        super().__init__(mode=SimulationMode.HANG, time_scale=0.01)
        self.stop_failed: bool = False
        self.shutting_down: bool = False

    @override
    async def stop(self) -> None:
        if self.shutting_down:
            return await super().stop()
        self.stop_failed = True
        raise RuntimeError("simulated stop acknowledgement failure")

    @override
    async def disconnect(self) -> None:
        # The fixture can power down even when its normal stop command fails.
        self.shutting_down = True
        self.stop_failed = False
        await super().disconnect()

    @override
    async def get_state(self) -> RobotState:
        state = await super().get_state()
        if self.stop_failed:
            return replace(state, is_moving=True)
        return state


class UnconfirmedStopAdapter(UnstoppableAdapter):
    @override
    async def stop(self) -> None:
        if self.shutting_down:
            return await SimulatedAdapter.stop(self)
        # Accepting the command alone is not evidence that motion stopped.
        self.stop_failed: bool = True


class GatedStopAdapter(SimulatedAdapter):
    def __init__(self) -> None:
        super().__init__(mode=SimulationMode.HANG)
        self.stop_entered: asyncio.Event = asyncio.Event()
        self.allow_stop: asyncio.Event = asyncio.Event()
        self.move_calls: int = 0

    @override
    async def move_relative(self, distance_m: float, speed_m_s: float) -> None:
        self.move_calls += 1
        await super().move_relative(distance_m, speed_m_s)

    @override
    async def stop(self) -> None:
        self.stop_entered.set()
        _ = await self.allow_stop.wait()
        await super().stop()

    @override
    async def disconnect(self) -> None:
        self.allow_stop.set()
        await super().disconnect()


class GatedConnectAdapter(SimulatedAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.connect_entered: asyncio.Event = asyncio.Event()
        self.allow_connect: asyncio.Event = asyncio.Event()
        self.connect_calls: int = 0
        self.disconnect_calls: int = 0

    @override
    async def connect(self) -> None:
        self.connect_calls += 1
        # Model a partially opened SDK connection before initialization finishes.
        await super().connect()
        self.connect_entered.set()
        _ = await self.allow_connect.wait()

    @override
    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        await super().disconnect()


class DeviceTimeoutAdapter(SimulatedAdapter):
    @override
    async def move_relative(self, distance_m: float, speed_m_s: float) -> None:
        raise TimeoutError("SDK movement acknowledgement timed out")


class FalseCompletionAdapter(SimulatedAdapter):
    @override
    async def move_relative(self, distance_m: float, speed_m_s: float) -> None:
        # A driver returning successfully cannot substitute for arrival evidence.
        return


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    @overload
    def make_runtime[T: SimulatedAdapter](
        self, mode: SimulationMode = SimulationMode.SUCCESS, *, robot: T
    ) -> tuple[ActionManager, T, InMemoryStore]: ...

    @overload
    def make_runtime(
        self, mode: SimulationMode = SimulationMode.SUCCESS, *, robot: None = None
    ) -> tuple[ActionManager, SimulatedAdapter, InMemoryStore]: ...

    def make_runtime(
        self,
        mode: SimulationMode = SimulationMode.SUCCESS,
        *,
        robot: SimulatedAdapter | None = None,
    ) -> tuple[ActionManager, SimulatedAdapter, InMemoryStore]:
        robot = robot or SimulatedAdapter(mode=mode, time_scale=0.01)
        store = InMemoryStore()
        runtime = ActionManager(
            robot,
            SkillRegistry([MoveRelativeSkill()]),
            store=store,
            cleanup_timeout_s=0.1,
        )
        return runtime, robot, store

    def request(
        self, *, task_id: int = 1, distance: float = 0.3, timeout: float = 1.0
    ) -> ActionRequest:
        return ActionRequest(
            task_id=task_id,
            skill_name="move_relative",
            args={"distance_m": distance, "speed_m_s": 0.2},
            timeout_s=timeout,
        )

    async def wait_until_running(
        self, runtime: ActionManager, robot: SimulatedAdapter, action_id: int
    ) -> None:
        async with asyncio.timeout(2):
            while True:
                record = runtime.get_action(action_id)
                state = await robot.get_state()
                if record.status is ActionStatus.RUNNING and state.is_moving:
                    return
                if record.status in {
                    ActionStatus.SUCCEEDED,
                    ActionStatus.FAILED,
                    ActionStatus.CANCELLED,
                    ActionStatus.TIMED_OUT,
                }:
                    self.fail(f"action ended before it started moving: {record}")
                await asyncio.sleep(0.001)

    async def terminal(self, runtime: ActionManager, action_id: int) -> ActionRecord:
        async with asyncio.timeout(2):
            return await runtime.wait_for_action(action_id)

    async def test_success_has_verified_position_and_simulation_evidence(self) -> None:
        runtime, robot, _ = self.make_runtime()
        async with runtime:
            submitted = await runtime.submit_action(self.request())
            self.assertIs(submitted.status, ActionStatus.QUEUED)
            self.assertIsNone(submitted.started_at)
            self.assertIsNone(submitted.ended_at)

            completed = await self.terminal(runtime, submitted.action_id)
            state = await robot.get_state()

            self.assertIs(completed.status, ActionStatus.SUCCEEDED)
            assert completed.result is not None
            assert completed.started_at is not None
            assert completed.ended_at is not None
            self.assertIs(completed.result.evidence["simulated"], True)
            self.assertAlmostEqual(state.position.x, 0.3)
            self.assertFalse(state.is_moving)
            self.assertIsNone(completed.failure)
            self.assertLessEqual(completed.created_at, completed.started_at)
            self.assertLessEqual(completed.started_at, completed.ended_at)

    async def test_device_failure_is_recorded_and_stopped(self) -> None:
        runtime, robot, _ = self.make_runtime(SimulationMode.FAILURE)
        async with runtime:
            submitted = await runtime.submit_action(self.request())
            completed = await self.terminal(runtime, submitted.action_id)
            self.assertIs(completed.status, ActionStatus.FAILED)
            self.assertTrue(completed.failure)
            self.assertFalse((await robot.get_state()).is_moving)

    async def test_timeout_stops_a_hanging_device(self) -> None:
        runtime, robot, _ = self.make_runtime(SimulationMode.HANG)
        async with runtime:
            submitted = await runtime.submit_action(self.request(timeout=0.03))
            completed = await self.terminal(runtime, submitted.action_id)
            self.assertIs(completed.status, ActionStatus.TIMED_OUT)
            self.assertTrue(completed.failure)
            self.assertFalse((await robot.get_state()).is_moving)

    async def test_running_cancellation_waits_for_stop(self) -> None:
        runtime, robot, _ = self.make_runtime(SimulationMode.HANG)
        async with runtime:
            submitted = await runtime.submit_action(self.request())
            await self.wait_until_running(runtime, robot, submitted.action_id)
            _ = await runtime.cancel_action(submitted.action_id)
            completed = await self.terminal(runtime, submitted.action_id)
            self.assertIs(completed.status, ActionStatus.CANCELLED)
            self.assertFalse((await robot.get_state()).is_moving)

    async def test_queued_cancellation_does_not_stop_running_action(self) -> None:
        runtime, robot, _ = self.make_runtime(SimulationMode.HANG)
        async with runtime:
            first = await runtime.submit_action(self.request())
            await self.wait_until_running(runtime, robot, first.action_id)
            second = await runtime.submit_action(self.request(distance=0.1))

            _ = await runtime.cancel_action(second.action_id)
            cancelled = await self.terminal(runtime, second.action_id)

            self.assertIs(cancelled.status, ActionStatus.CANCELLED)
            self.assertIsNone(cancelled.started_at)
            self.assertIs(
                runtime.get_action(first.action_id).status, ActionStatus.RUNNING
            )
            self.assertTrue((await robot.get_state()).is_moving)

            _ = await runtime.cancel_action(first.action_id)
            _ = await self.terminal(runtime, first.action_id)

    async def test_actions_execute_serially(self) -> None:
        runtime, robot, _ = self.make_runtime()
        async with runtime:
            first = await runtime.submit_action(self.request(distance=0.3))
            second = await runtime.submit_action(self.request(distance=0.2))
            first_done, second_done = await asyncio.gather(
                self.terminal(runtime, first.action_id),
                self.terminal(runtime, second.action_id),
            )
            self.assertIs(first_done.status, ActionStatus.SUCCEEDED)
            self.assertIs(second_done.status, ActionStatus.SUCCEEDED)
            assert first_done.ended_at is not None
            assert second_done.started_at is not None
            self.assertLessEqual(first_done.ended_at, second_done.started_at)
            self.assertAlmostEqual((await robot.get_state()).position.x, 0.5)

    async def test_invalid_requests_are_rejected_without_creating_actions(self) -> None:
        runtime, _, _ = self.make_runtime()
        cases = [
            replace(self.request(), skill_name="does_not_exist"),
            replace(self.request(), args={}),
            replace(
                self.request(), args={"distance_m": 0.2, "speed_m_s": 0.1, "extra": 1}
            ),
        ]
        for invalid in (
            True,
            "0.2",
            float("nan"),
            float("inf"),
            -float("inf"),
            2.01,
            -2.01,
        ):
            cases.append(
                replace(self.request(), args={"distance_m": invalid, "speed_m_s": 0.2})
            )
        for invalid in (True, "0.2", float("nan"), float("inf"), -0.1, 0, 0.51):
            cases.append(
                replace(self.request(), args={"distance_m": 0.2, "speed_m_s": invalid})
            )
        for invalid in (True, "1", float("nan"), float("inf"), -1, 0):
            # Deliberately cross the static contract to exercise runtime validation.
            cases.append(replace(self.request(), timeout_s=cast(float, invalid)))

        async with runtime:
            for request in cases:
                with self.subTest(
                    skill=request.skill_name,
                    args=request.args,
                    timeout=request.timeout_s,
                ):
                    with self.assertRaises((ValueError, TypeError, KeyError)):
                        _ = await runtime.submit_action(request)
                    self.assertEqual(runtime.list_actions(), [])

    async def test_request_and_record_snapshots_do_not_mutate_runtime(self) -> None:
        runtime, robot, _ = self.make_runtime()
        async with runtime:
            request = self.request(distance=0.3)
            submitted = await runtime.submit_action(request)
            request.args["distance_m"] = 1.0
            submitted.raw_request.args["distance_m"] = 1.5

            snapshot = runtime.get_action(submitted.action_id)
            snapshot.raw_request.args["distance_m"] = 2.0
            records = runtime.list_actions()
            records[0].raw_request.args["distance_m"] = -2.0
            records.clear()

            completed = await self.terminal(runtime, submitted.action_id)
            self.assertIs(completed.status, ActionStatus.SUCCEEDED)
            self.assertEqual(completed.raw_request.args["distance_m"], 0.3)
            self.assertAlmostEqual((await robot.get_state()).position.x, 0.3)
            self.assertEqual(len(runtime.list_actions()), 1)
            assert completed.result is not None
            completed.result.evidence["simulated"] = False
            stored_result = runtime.get_action(submitted.action_id).result
            assert stored_result is not None
            self.assertIs(stored_result.evidence["simulated"], True)

    async def test_cancelling_a_waiter_does_not_cancel_action(self) -> None:
        runtime, robot, _ = self.make_runtime(SimulationMode.HANG)
        async with runtime:
            submitted = await runtime.submit_action(self.request())
            await self.wait_until_running(runtime, robot, submitted.action_id)
            waiter = asyncio.create_task(runtime.wait_for_action(submitted.action_id))
            await asyncio.sleep(0)
            _ = waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                _ = await waiter
            self.assertIs(
                runtime.get_action(submitted.action_id).status, ActionStatus.RUNNING
            )
            self.assertTrue((await robot.get_state()).is_moving)

            _ = await runtime.cancel_action(submitted.action_id)
            completed = await self.terminal(runtime, submitted.action_id)
            self.assertIs(completed.status, ActionStatus.CANCELLED)

    async def test_shutdown_cancels_running_and_queued_actions_and_disconnects(
        self,
    ) -> None:
        runtime, robot, _ = self.make_runtime(SimulationMode.HANG)
        async with runtime:
            first = await runtime.submit_action(self.request())
            await self.wait_until_running(runtime, robot, first.action_id)
            second = await runtime.submit_action(self.request())

        self.assertIs(
            runtime.get_action(first.action_id).status, ActionStatus.CANCELLED
        )
        self.assertIs(
            runtime.get_action(second.action_id).status, ActionStatus.CANCELLED
        )
        state = await robot.get_state()
        self.assertFalse(state.is_moving)
        self.assertFalse(state.is_connected)
        with self.assertRaises(RuntimeError):
            _ = await runtime.submit_action(self.request())

    async def test_unconfirmed_stop_blocks_queued_and_future_movement(self) -> None:
        for adapter_type in (UnstoppableAdapter, UnconfirmedStopAdapter):
            with self.subTest(adapter=adapter_type.__name__):
                runtime, robot, _ = self.make_runtime(robot=adapter_type())
                async with runtime:
                    first = await runtime.submit_action(self.request())
                    await self.wait_until_running(runtime, robot, first.action_id)
                    second = await runtime.submit_action(self.request())
                    _ = await runtime.cancel_action(first.action_id)

                    first_done = await self.terminal(runtime, first.action_id)
                    second_done = await self.terminal(runtime, second.action_id)
                    self.assertIs(first_done.status, ActionStatus.FAILED)
                    self.assertTrue(first_done.failure)
                    self.assertIs(second_done.status, ActionStatus.FAILED)
                    self.assertIsNone(second_done.started_at)
                    with self.assertRaises(RuntimeError):
                        _ = await runtime.submit_action(self.request())

    async def test_success_events_report_state_progression(self) -> None:
        runtime, _, _ = self.make_runtime()
        async with runtime:
            submitted = await runtime.submit_action(self.request())
            _ = await self.terminal(runtime, submitted.action_id)
            events: list[ActionEvent] = []
            async with asyncio.timeout(2):
                while not events or events[-1].status is not ActionStatus.SUCCEEDED:
                    events.append(await runtime.next_event())
            self.assertTrue(
                all(event.action_id == submitted.action_id for event in events)
            )
            self.assertEqual(
                [event.status for event in events],
                [
                    ActionStatus.QUEUED,
                    ActionStatus.RUNNING,
                    ActionStatus.VERIFYING,
                    ActionStatus.SUCCEEDED,
                ],
            )

    async def test_task_completion_is_explicit_after_all_actions_succeed(self) -> None:
        runtime, _, store = self.make_runtime()
        coordinator = TaskCoordinator(runtime, store)
        async with runtime:
            task = coordinator.create_task("演示向前移动")
            with self.assertRaises(ValueError):
                _ = coordinator.complete_task(task.task_id)
            submitted = await coordinator.submit_action(
                task.task_id, "move_relative", {"distance_m": 0.3, "speed_m_s": 0.2}
            )
            with self.assertRaises(ValueError):
                _ = coordinator.complete_task(task.task_id)

            _ = await self.terminal(runtime, submitted.action_id)
            self.assertIs(coordinator.get_task(task.task_id).status, TaskStatus.RUNNING)
            completed = coordinator.complete_task(task.task_id)
            self.assertIs(completed.status, TaskStatus.COMPLETED)
            self.assertIn(submitted.action_id, completed.action_ids)

    async def test_failed_action_prevents_successful_task_completion(self) -> None:
        runtime, _, store = self.make_runtime(SimulationMode.FAILURE)
        coordinator = TaskCoordinator(runtime, store)
        async with runtime:
            task = coordinator.create_task("演示失败")
            submitted = await coordinator.submit_action(
                task.task_id, "move_relative", {"distance_m": 0.3, "speed_m_s": 0.2}
            )
            _ = await self.terminal(runtime, submitted.action_id)
            self.assertIs(
                coordinator.complete_task(task.task_id).status, TaskStatus.FAILED
            )

    async def test_task_cancellation_cancels_all_its_actions_and_stops_device(
        self,
    ) -> None:
        runtime, robot, store = self.make_runtime(SimulationMode.HANG)
        coordinator = TaskCoordinator(runtime, store)
        async with runtime:
            task = coordinator.create_task("取消移动任务")
            first = await coordinator.submit_action(
                task.task_id, "move_relative", {"distance_m": 0.3, "speed_m_s": 0.2}
            )
            await self.wait_until_running(runtime, robot, first.action_id)
            second = await coordinator.submit_action(
                task.task_id, "move_relative", {"distance_m": 0.1, "speed_m_s": 0.2}
            )
            cancelled = await coordinator.cancel_task(task.task_id)
            self.assertIs(cancelled.status, TaskStatus.CANCELLED)
            self.assertIs(
                runtime.get_action(first.action_id).status, ActionStatus.CANCELLED
            )
            self.assertIs(
                runtime.get_action(second.action_id).status, ActionStatus.CANCELLED
            )
            self.assertFalse((await robot.get_state()).is_moving)

    async def test_task_cancellation_reports_failed_stop_as_failure(self) -> None:
        runtime, robot, store = self.make_runtime(robot=UnstoppableAdapter())
        coordinator = TaskCoordinator(runtime, store)
        async with runtime:
            task = coordinator.create_task("停止失败的任务")
            submitted = await coordinator.submit_action(
                task.task_id, "move_relative", {"distance_m": 0.3, "speed_m_s": 0.2}
            )
            await self.wait_until_running(runtime, robot, submitted.action_id)
            async with asyncio.timeout(2):
                finished = await coordinator.cancel_task(task.task_id)
            self.assertIs(finished.status, TaskStatus.FAILED)
            self.assertIs(
                runtime.get_action(submitted.action_id).status, ActionStatus.FAILED
            )

    async def test_cancelling_task_cancel_caller_does_not_interrupt_cleanup(
        self,
    ) -> None:
        runtime, robot, store = self.make_runtime(robot=GatedStopAdapter())
        coordinator = TaskCoordinator(runtime, store)
        async with runtime:
            task = coordinator.create_task("调用者退出后仍完成取消")
            submitted = await coordinator.submit_action(
                task.task_id, "move_relative", {"distance_m": 0.3, "speed_m_s": 0.2}
            )
            await self.wait_until_running(runtime, robot, submitted.action_id)
            caller = asyncio.create_task(coordinator.cancel_task(task.task_id))
            try:
                async with asyncio.timeout(2):
                    _ = await robot.stop_entered.wait()
                _ = caller.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    _ = await caller
                self.assertTrue((await robot.get_state()).is_moving)
                robot.allow_stop.set()
                completed = await self.terminal(runtime, submitted.action_id)
                self.assertIs(completed.status, ActionStatus.CANCELLED)
                async with asyncio.timeout(2):
                    while (
                        coordinator.get_task(task.task_id).status
                        is TaskStatus.CANCELLING
                    ):
                        await asyncio.sleep(0.001)
                self.assertIs(
                    coordinator.get_task(task.task_id).status, TaskStatus.CANCELLED
                )
                self.assertFalse((await robot.get_state()).is_moving)
            finally:
                robot.allow_stop.set()
                _ = await asyncio.gather(caller, return_exceptions=True)

    async def test_close_during_start_waits_for_connection_then_disconnects(
        self,
    ) -> None:
        baseline = asyncio.all_tasks()
        runtime, robot, _ = self.make_runtime(robot=GatedConnectAdapter())
        starting = asyncio.create_task(runtime.start())
        closing = None
        try:
            async with asyncio.timeout(2):
                _ = await robot.connect_entered.wait()
            closing = asyncio.create_task(runtime.close())
            # The explicit gate keeps connect suspended while close gets its turn.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertFalse(
                closing.done(), "close must wait for in-flight connection setup"
            )
            robot.allow_connect.set()
            async with asyncio.timeout(2):
                _ = await asyncio.gather(starting, closing, return_exceptions=True)
            self.assertFalse((await robot.get_state()).is_connected)
            self.assertGreaterEqual(robot.disconnect_calls, 1)
            await asyncio.sleep(0)
            self.assertEqual(asyncio.all_tasks() - baseline, set())
            with self.assertRaises(RuntimeError):
                _ = await runtime.submit_action(self.request())
        finally:
            robot.allow_connect.set()
            _ = await asyncio.gather(starting, return_exceptions=True)
            if closing is not None:
                _ = await asyncio.gather(closing, return_exceptions=True)
            await runtime.close()

    async def test_concurrent_start_connects_once_and_uses_one_executor(self) -> None:
        baseline = asyncio.all_tasks()
        runtime, robot, _ = self.make_runtime(robot=GatedConnectAdapter())
        first_start = asyncio.create_task(runtime.start())
        second_start = None
        try:
            async with asyncio.timeout(2):
                _ = await robot.connect_entered.wait()
            second_start = asyncio.create_task(runtime.start())
            await asyncio.sleep(0)
            robot.allow_connect.set()
            async with asyncio.timeout(2):
                _ = await asyncio.gather(first_start, second_start)
            self.assertEqual(robot.connect_calls, 1)
            first = await runtime.submit_action(self.request(distance=0.3))
            second = await runtime.submit_action(self.request(distance=0.2))
            first_done, second_done = await asyncio.gather(
                self.terminal(runtime, first.action_id),
                self.terminal(runtime, second.action_id),
            )
            self.assertIs(first_done.status, ActionStatus.SUCCEEDED)
            self.assertIs(second_done.status, ActionStatus.SUCCEEDED)
            assert first_done.ended_at is not None
            assert second_done.started_at is not None
            self.assertLessEqual(first_done.ended_at, second_done.started_at)
            self.assertAlmostEqual((await robot.get_state()).position.x, 0.5)
        finally:
            robot.allow_connect.set()
            starts = [first_start] + ([second_start] if second_start else [])
            _ = await asyncio.gather(*starts, return_exceptions=True)
            async with asyncio.timeout(2):
                await runtime.close()
        await asyncio.sleep(0)
        self.assertFalse((await robot.get_state()).is_connected)
        self.assertEqual(asyncio.all_tasks() - baseline, set())

    async def test_cancelled_start_cleans_up_partially_open_connection(self) -> None:
        baseline = asyncio.all_tasks()
        runtime, robot, _ = self.make_runtime(robot=GatedConnectAdapter())
        starting = asyncio.create_task(runtime.start())
        try:
            async with asyncio.timeout(2):
                _ = await robot.connect_entered.wait()
            self.assertTrue((await robot.get_state()).is_connected)
            _ = starting.cancel()
            async with asyncio.timeout(2):
                with self.assertRaises(asyncio.CancelledError):
                    await starting
            self.assertFalse((await robot.get_state()).is_connected)
            self.assertEqual(robot.disconnect_calls, 1)
            await asyncio.sleep(0)
            self.assertEqual(asyncio.all_tasks() - baseline, set())
        finally:
            robot.allow_connect.set()
            _ = await asyncio.gather(starting, return_exceptions=True)
            await runtime.close()

    async def test_sdk_timeout_is_failure_and_preserves_source_error(self) -> None:
        runtime, robot, _ = self.make_runtime(robot=DeviceTimeoutAdapter())
        async with runtime:
            submitted = await runtime.submit_action(self.request(timeout=10))
            completed = await self.terminal(runtime, submitted.action_id)
            self.assertIs(completed.status, ActionStatus.FAILED)
            assert completed.failure is not None
            self.assertIn("TimeoutError", completed.failure)
            self.assertIn("SDK movement acknowledgement timed out", completed.failure)
            self.assertFalse((await robot.get_state()).is_moving)

    async def test_completion_requires_observed_arrival(self) -> None:
        runtime, robot, _ = self.make_runtime(robot=FalseCompletionAdapter())
        async with runtime:
            submitted = await runtime.submit_action(self.request(distance=0.3))
            completed = await self.terminal(runtime, submitted.action_id)
            self.assertIs(completed.status, ActionStatus.FAILED)
            self.assertIsNone(completed.result)
            assert completed.failure is not None
            self.assertIn("verification", completed.failure.lower())
            self.assertAlmostEqual((await robot.get_state()).position.x, 0.0)

    async def test_cleanup_timeout_blocks_subsequent_actions(self) -> None:
        runtime, robot, _ = self.make_runtime(robot=GatedStopAdapter())
        async with runtime:
            first = await runtime.submit_action(self.request())
            await self.wait_until_running(runtime, robot, first.action_id)
            second = await runtime.submit_action(self.request())
            _ = await runtime.cancel_action(first.action_id)
            first_done = await self.terminal(runtime, first.action_id)
            second_done = await self.terminal(runtime, second.action_id)
            self.assertIs(first_done.status, ActionStatus.FAILED)
            assert first_done.failure is not None
            self.assertIn("cleanup", first_done.failure.lower())
            self.assertIs(second_done.status, ActionStatus.FAILED)
            self.assertIsNone(second_done.started_at)
            self.assertEqual(robot.move_calls, 1)
            self.assertTrue(runtime.blocked_reason)
            with self.assertRaises(RuntimeError):
                _ = await runtime.submit_action(self.request())


if __name__ == "__main__":
    _ = unittest.main()
