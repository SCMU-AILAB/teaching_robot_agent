# perception/camera/base.py
"""相机基类：统一状态与单据流程，子类只负责取一帧原始数据."""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from domain.services import FrameReference
from storage.evidence import EvidenceStore


@dataclass(frozen=True)
class RawFrame:
    """一帧编码后的原始数据，尺寸与格式都来自它自己."""

    data: bytes
    width: int
    height: int
    media_type: str


class BaseCamera(ABC):
    """相机实现的状态与采集流程，差异部分由子类补齐."""

    def __init__(self, _evidence: EvidenceStore, _camera_id: str) -> None:
        """记录证据存储与相机标识."""
        self._evidence: EvidenceStore = _evidence
        self._camera_id: str = _camera_id
        self._next_frame_seq: int = 1
        self._closed: bool = True
        self._lock: asyncio.Lock = asyncio.Lock()

    async def connect(self) -> None:
        """打开相机；已经打开时直接返回，不重复占用设备."""
        if not self._closed:
            return
        async with self._lock:
            await self.open_device()
            self._closed = False

    async def capture(self, scene_revision: int) -> FrameReference:
        """取一帧、登记证据并返回帧引用.

        Args:
            scene_revision: 这一帧属于第几版场景，原样写入帧引用.

        Returns:
            字段完整的帧引用，证据编号可用于回读原始图片.

        Raises:
            RuntimeError: 相机已经关闭时抛出.
        """
        async with self._lock:
            if self._closed:
                raise RuntimeError("camera is closed")
            raw = await self.grab_frame()
            captured_at = time.time()
            receipt = await self._evidence.save(raw.data, raw.media_type, captured_at)
            frame_id = f"{self._camera_id}:{self._next_frame_seq}"
            self._next_frame_seq += 1
            return FrameReference(
                frame_id=frame_id,
                camera_id=self._camera_id,
                captured_at=captured_at,
                width=raw.width,
                height=raw.height,
                evidence_id=receipt.evidence_id,
                scene_revision=scene_revision,
            )

    async def close(self) -> None:
        """释放相机资源，重复调用安全."""
        if self._closed:
            return
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            await self.release_device()

    @abstractmethod
    async def open_device(self) -> None:
        """子类在这里真正地打开设备."""

    @abstractmethod
    async def grab_frame(self) -> RawFrame:
        """子类在这里给出一帧编码后的原始数据."""

    @abstractmethod
    async def release_device(self) -> None:
        """子类在这里释放真实资源；没有真资源时写明原因即可."""
