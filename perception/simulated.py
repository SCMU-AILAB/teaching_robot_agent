# perception/simulated.py
"""无需相机或模型即可联调的感知模拟实现."""

import base64
import time
from uuid import uuid4

from domain.services import (
    FrameReference,
    ObservationRequest,
    ObservationResult,
    VisionAnalysis,
)
from domain.validation import finite_float, nonempty_string, positive_int
from storage.evidence import EvidenceStore

_IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)


class SimulatedPerception:
    """返回明确标记为模拟的观察，不模拟真实物体识别."""

    def __init__(self, _evidence: EvidenceStore) -> None:
        """注入核心证据存储."""
        self._evidence: EvidenceStore = _evidence
        self._revision: int = 0
        self._closed: bool = False

    def invalidate(self, reason: str) -> int:
        """使场景版本递增，便于联调移动导致的观察失效."""
        _ = nonempty_string(reason, "reason")
        self._revision += 1
        return self._revision

    async def observe(self, request: ObservationRequest) -> ObservationResult:
        """产生新模拟图片和观察，每次采集而不伪装缓存命中."""
        if self._closed:
            raise RuntimeError("Perception is closed")
        _ = positive_int(request.task_id, "task_id")
        _ = nonempty_string(request.question, "question")
        age = finite_float(request.max_age_s, "max_age_s")
        timeout = finite_float(request.timeout_s, "timeout_s")
        if not 0 <= age <= 30 or not 0 < timeout <= 120:
            raise ValueError("Invalid observation limits")
        captured_at = time.time()
        evidence = await self._evidence.save(_IMAGE, "image/png", captured_at)
        frame = FrameReference(
            uuid4().hex,
            "simulated-camera",
            captured_at,
            1,
            1,
            evidence.evidence_id,
            self._revision,
        )
        return ObservationResult(
            request.observation_id,
            request.task_id,
            request.question,
            frame,
            VisionAnalysis("模拟观察，仅供接口联调。", "simulated-vlm", True),
            time.time(),
            False,
            time.time() - captured_at > age,
        )

    async def close(self) -> None:
        """关闭模拟感知服务."""
        self._closed = True
