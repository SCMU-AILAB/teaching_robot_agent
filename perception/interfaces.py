# perception/interfaces.py
"""感知负责人实现的异步接口，核心仅依赖这些结构化契约."""

from typing import Protocol

from domain.services import (
    FrameReference,
    ObservationRequest,
    ObservationResult,
    VisionAnalysis,
    VisionRequest,
)


class CameraSource(Protocol):
    """相机资源由感知模块独占管理."""

    async def connect(self) -> None:
        """建立相机连接."""
        ...

    async def capture(self, scene_revision: int) -> FrameReference:
        """采集新帧并登记证据，填写实际采集时间."""
        ...

    async def close(self) -> None:
        """释放相机资源，重复调用安全."""
        ...


class VisionProvider(Protocol):
    """隔离本地宇树模型的具体协议，不要求云服务或工具调用能力."""

    async def analyze(self, request: VisionRequest) -> VisionAnalysis:
        """读取图像证据并返回经过校验的观察，取消继续向外传播."""
        ...


class PerceptionService(Protocol):
    """提供缓存、失效与总超时管理的场景观察入口."""

    async def observe(self, request: ObservationRequest) -> ObservationResult:
        """返回一次观察，失败抛出异常，不伪装成空场景."""
        ...

    def invalidate(self, reason: str) -> int:
        """递增场景版本，使运动前的缓存失效."""
        ...

    async def close(self) -> None:
        """取消感知工作并释放资源."""
        ...
