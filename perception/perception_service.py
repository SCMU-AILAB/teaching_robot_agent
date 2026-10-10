# perception/perception_service.py
"""真实感知管家：组合相机、视觉模型与场景账本，负责缓存、失效与总超时."""

import asyncio
import time

from domain.services import ObservationRequest, ObservationResult, VisionRequest
from domain.validation import finite_float, nonempty_string, positive_int
from perception.interfaces import CameraSource, VisionProvider
from perception.scene_store import SceneStore


class LivePerception:
    """组合相机、视觉模型与场景账本，按钥匙复用观察并在总时限内返回."""

    def __init__(
        self,
        _camera: CameraSource,
        _provider: VisionProvider,
        _store: SceneStore,
        _camera_id: str,
        _model_id: str,
    ) -> None:
        """注入相机、视觉模型、账本和本机身份标识."""
        self._camera: CameraSource = _camera
        self._provider: VisionProvider = _provider
        self._store: SceneStore = _store
        self._camera_id: str = nonempty_string(_camera_id, "camera_id")
        self._model_id: str = nonempty_string(_model_id, "model_id")
        self._revision: int = 0
        self._closed: bool = False
        self._tasks: set[asyncio.Task[ObservationResult]] = set()

    def invalidate(self, reason: str) -> int:
        """场景版本加一并返回新版本，并且清楚旧版账本信息."""
        _ = nonempty_string(reason, "reason")
        self._revision += 1
        _ = self._store.drop_stale(self._revision)
        return self._revision

    async def observe(self, request: ObservationRequest) -> ObservationResult:
        """返回一次观察，失败抛出异常，不伪装成空场景."""
        if self._closed:
            raise RuntimeError("Perception is closed")

        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            task_id = positive_int(request.task_id, "task_id")
            question = nonempty_string(request.question, "question")
            observation_id = nonempty_string(request.observation_id, "observation_id")
            age = finite_float(request.max_age_s, "max_age_s")
            timeout = finite_float(request.timeout_s, "timeout_s")
            if not 0 <= age <= 30 or not 0 < timeout <= 120:
                raise ValueError("Invalid observation limits")
            started = time.monotonic()
            revision = self._revision
            record = self._store.find(
                _question=question,
                _camera_id=self._camera_id,
                _model_id=self._model_id,
                _scene_revision=revision,
            )
            if record is not None:
                if time.time() - record.captured_at <= age:
                    return ObservationResult(
                        observation_id=observation_id,
                        task_id=task_id,
                        question=question,
                        frame=record.frame,
                        analysis=record.analysis,
                        analyzed_at=record.analyzed_at,
                        from_cache=True,
                        stale=False,
                    )
            async with asyncio.timeout(timeout):
                frame = await self._camera.capture(revision)
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("observation budget exhausted")
                analysis = await self._provider.analyze(
                    VisionRequest(observation_id, question, frame, remaining)
                )
            stale = revision < self._revision
            analyzed_at = time.time()
            if not stale:
                await self._store.remember(
                    _question=question,
                    _frame=frame,
                    _analysis=analysis,
                    _analyzed_at=analyzed_at,
                )
            return ObservationResult(
                observation_id=observation_id,
                task_id=task_id,
                question=question,
                frame=frame,
                analysis=analysis,
                analyzed_at=analyzed_at,
                from_cache=False,
                stale=stale,
            )
        finally:
            self._tasks.discard(task)

    async def close(self) -> None:
        """取消在途观察并释放相机，重复调用安全.

        取消的是在途观察【所属的任务】：若调用方用 await 与它共享同一个任务
        （app/team_gateway.py:205 就是这种），调用方的等待会被一并取消。

        取消采集不等于驱动立刻停下：BaseCamera 会等驱动调用真正退出后才释放
        设备，真机接入需要驱动级超时或隔离进程；本次未连真实摄像头验证。
        """
        if self._closed:
            return
        self._closed = True
        for task in list(self._tasks):
            _ = task.cancel()
        if self._tasks:
            _ = await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._camera.close()
