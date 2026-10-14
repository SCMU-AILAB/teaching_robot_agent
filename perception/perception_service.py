# perception/perception_service.py
"""真实感知管家：组合相机、视觉模型与场景账本，负责缓存、失效与总超时."""

import asyncio
import time

from domain.services import (
    FrameReference,
    ObservationRequest,
    ObservationResult,
    VisionAnalysis,
    VisionRequest,
)
from domain.validation import finite_float, nonempty_string, positive_int
from perception.interfaces import CameraSource, VisionProvider
from perception.scene_store import SceneStore
from storage.evidence import EvidenceStore


class LivePerception:
    """组合相机、视觉模型与场景账本，按钥匙复用观察并在总时限内返回."""

    def __init__(
        self,
        _camera: CameraSource,
        _provider: VisionProvider,
        _store: SceneStore,
        _evidence: EvidenceStore,
        _camera_id: str,
        _model_id: str,
    ) -> None:
        """注入相机、视觉模型、账本、证据存储和本机身份标识."""
        self._camera: CameraSource = _camera
        self._provider: VisionProvider = _provider
        self._store: SceneStore = _store
        self._evidence: EvidenceStore = _evidence
        self._camera_id: str = nonempty_string(_camera_id, "camera_id")
        self._model_id: str = nonempty_string(_model_id, "model_id")
        self._revision: int = 0
        self._closed: bool = False
        # 只登记服务自己创建的作业，关闭时不会取消调用方的任务。
        self._jobs: set[asyncio.Task[tuple[FrameReference, VisionAnalysis]]] = set()

    def invalidate(self, reason: str) -> int:
        """场景版本加一并返回新版本，并且清除旧版账本信息."""
        _ = nonempty_string(reason, "reason")
        self._revision += 1
        _ = self._store.drop_stale(self._revision)
        return self._revision

    async def observe(self, request: ObservationRequest) -> ObservationResult:
        """返回一次观察，失败抛出异常，不伪装成空场景.

        Args:
            request: 核心提交的观察请求.

        Returns:
            缓存命中或本次采集推理得到的观察结果.

        Raises:
            ValueError: 请求参数非法，或帧与模型标识与本服务配置不一致.
            TimeoutError: 采集与推理超过总时限.
            RuntimeError: 服务已关闭，或观察进行中服务被关闭.
            asyncio.CancelledError: 调用方取消，作业已真正退出.
        """
        if self._closed:
            raise RuntimeError("Perception is closed")
        task_id = positive_int(request.task_id, "task_id")
        question = nonempty_string(request.question, "question")
        observation_id = nonempty_string(request.observation_id, "observation_id")
        age = finite_float(request.max_age_s, "max_age_s")
        timeout = finite_float(request.timeout_s, "timeout_s")
        if not 0 <= age <= 30 or not 0 < timeout <= 120:
            raise ValueError("Invalid observation limits")
        revision = self._revision
        record = self._store.find(
            _question=question,
            _camera_id=self._camera_id,
            _model_id=self._model_id,
            _scene_revision=revision,
        )
        if (
            record is not None
            and time.time() - record.captured_at <= age
            and self._evidence_available(record.frame.evidence_id)
        ):
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
        job = asyncio.create_task(
            self._capture_and_analyze(observation_id, question, revision, timeout)
        )
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)
        try:
            # 直接等待作业：调用方取消会传到作业，并等作业真正退出后才返回。
            frame, analysis = await job
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            # 调用方未被取消，说明是 close() 取消了作业，不向调用方注入取消。
            raise RuntimeError("Perception closed during observation") from None
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

    def _evidence_available(self, evidence_id: str) -> bool:
        """缓存帧的证据可能已随任务结束释放，释放后不能再作为观察返回."""
        try:
            _ = self._evidence.get(evidence_id)
        except KeyError:
            return False
        return True

    async def _capture_and_analyze(
        self, observation_id: str, question: str, revision: int, timeout: float
    ) -> tuple[FrameReference, VisionAnalysis]:
        """在总时限内采集并推理；帧未交出前失败则释放其证据.

        Args:
            observation_id: 本次观察编号，作为视觉请求编号.
            question: 已校验的完整问题.
            revision: 采集时的场景版本.
            timeout: 采集与推理共享的总时限，秒.

        Returns:
            本次采集的帧与经校验的观察结论.

        Raises:
            TimeoutError: 超过总时限.
            ValueError: 帧或结论的标识与本服务配置不一致.
        """
        started = time.monotonic()
        frame: FrameReference | None = None
        try:
            async with asyncio.timeout(timeout):
                frame = await self._camera.capture(revision)
                if frame.camera_id != self._camera_id:
                    raise ValueError("Frame camera does not match perception camera")
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("observation budget exhausted")
                analysis = await self._provider.analyze(
                    VisionRequest(observation_id, question, frame, remaining)
                )
                if analysis.model_id != self._model_id:
                    raise ValueError("Analysis model does not match perception model")
        except BaseException:
            # 失败的帧没有交给任何调用方，由感知负责释放，避免占满证据容量。
            if frame is not None:
                self._evidence.release(frame.evidence_id)
            raise
        return frame, analysis

    async def close(self) -> None:
        """取消服务自己的在途作业并释放相机，重复调用安全.

        取消采集不等于驱动立刻停下：BaseCamera 会等驱动调用真正退出后才释放
        设备，真机接入需要驱动级超时或隔离进程；本次未连真实摄像头验证。
        """
        if self._closed:
            return
        self._closed = True
        jobs = list(self._jobs)
        for job in jobs:
            _ = job.cancel()
        try:
            if jobs:
                _ = await asyncio.gather(*jobs, return_exceptions=True)
        finally:
            await self._camera.close()
