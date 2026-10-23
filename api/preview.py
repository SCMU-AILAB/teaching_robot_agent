# api/preview.py
"""摄像头预览：常驻采集最近一帧供前端轮询，无人轮询时自动停采."""

import asyncio
import io
import logging
import time
from dataclasses import dataclass

from PIL import Image

from api.errors import ApiError, ErrorCode
from domain.validation import finite_float
from perception.interfaces import CameraSource
from storage.evidence import EvidenceStore

logger = logging.getLogger(__name__)

# 预览帧不参与观察，场景版本固定为 0。
_PREVIEW_REVISION = 0


@dataclass(frozen=True)
class PreviewFrame:
    """最近一帧 JPEG 与采集时间."""

    jpeg: bytes
    width: int
    captured_at: float


@dataclass(frozen=True)
class CameraStatus:
    """SystemResponse.camera."""

    available: bool
    last_frame_at: float | None
    error: str | None


def _to_jpeg(content: bytes, max_width: int | None) -> tuple[bytes, int]:
    """转为 JPEG，可选等比缩小；在线程中执行."""
    with Image.open(io.BytesIO(content)) as image:
        frame = image.convert("RGB")
        if max_width is not None and frame.width > max_width:
            height = max(1, round(frame.height * max_width / frame.width))
            frame = frame.resize((max_width, height), Image.Resampling.BILINEAR)
        buffer = io.BytesIO()
        frame.save(buffer, "JPEG", quality=80)
        return buffer.getvalue(), frame.width


class CameraPreview:
    """独占一个采集循环；与感知共用相机，由相机自身的锁串行化采集."""

    def __init__(
        self,
        _camera: CameraSource,
        _evidence: EvidenceStore,
        *,
        _interval_s: float = 0.2,
        _idle_s: float = 10.0,
        _warmup_frames: int = 3,
        _first_frame_timeout_s: float = 3.0,
    ) -> None:
        """配置采集间隔、空闲停采时间与预热丢弃帧数，构造期间不打开设备.

        Args:
            _camera: 已由应用连接的相机.
            _evidence: 相机登记帧所用的证据存储；预览帧读取后立即释放.
            _interval_s: 采集间隔，秒.
            _idle_s: 超过该时长无人请求则停止采集，秒.
            _warmup_frames: 每次开始采集后丢弃的帧数，等待自动曝光与白平衡收敛.
            _first_frame_timeout_s: 首帧最长等待，秒.
        """
        self._camera: CameraSource = _camera
        self._evidence: EvidenceStore = _evidence
        self._interval_s: float = finite_float(_interval_s, "interval_s")
        self._idle_s: float = finite_float(_idle_s, "idle_s")
        if isinstance(_warmup_frames, bool) or _warmup_frames < 0:
            raise ValueError("warmup_frames must be a nonnegative integer")
        self._warmup: int = _warmup_frames
        self._first_timeout_s: float = finite_float(
            _first_frame_timeout_s, "first_frame_timeout_s"
        )
        self._latest: PreviewFrame | None = None
        self._error: str | None = None
        self._requested_at: float = 0.0
        self._fresh: asyncio.Event = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._closed: bool = False

    def status(self) -> CameraStatus:
        """只读内部状态，不触发采集."""
        if self._error is not None:
            return CameraStatus(False, self._last_frame_at(), self._error)
        return CameraStatus(True, self._last_frame_at(), None)

    def _last_frame_at(self) -> float | None:
        """最近一帧采集时间."""
        return self._latest.captured_at if self._latest is not None else None

    async def snapshot(self, max_width: int | None) -> bytes:
        """返回最近一帧 JPEG；采集未运行时启动并等待首帧.

        Raises:
            ApiError: 50002，相机已关闭、首帧未就绪或采集失败.
        """
        if self._closed:
            raise ApiError(ErrorCode.UNAVAILABLE, "摄像头已关闭")
        self._requested_at = time.monotonic()
        if self._worker is None or self._worker.done():
            self._latest = None
            self._fresh.clear()
            self._worker = asyncio.create_task(self._run(), name="camera-preview")
        if self._latest is None:
            try:
                async with asyncio.timeout(self._first_timeout_s):
                    _ = await self._fresh.wait()
            except TimeoutError:
                pass
        frame = self._latest
        if frame is None:
            raise ApiError(
                ErrorCode.UNAVAILABLE, self._error or "摄像头首帧未就绪，请稍后重试"
            )
        if max_width is None or frame.width <= max_width:
            return frame.jpeg
        jpeg, _ = await asyncio.to_thread(_to_jpeg, frame.jpeg, max_width)
        return jpeg

    async def _run(self) -> None:
        """采集到空闲超时为止；单帧失败记录错误后继续重试."""
        skipped = 0
        while time.monotonic() - self._requested_at < self._idle_s:
            try:
                frame = await self._camera.capture(_PREVIEW_REVISION)
                try:
                    content = await self._evidence.read(frame.evidence_id)
                finally:
                    self._evidence.release(frame.evidence_id)
                if skipped < self._warmup:
                    skipped += 1
                else:
                    jpeg, width = await asyncio.to_thread(_to_jpeg, content, None)
                    self._latest = PreviewFrame(jpeg, width, frame.captured_at)
                    self._error = None
                    self._fresh.set()
            except Exception as error:
                self._error = f"摄像头采集失败：{type(error).__name__}"
                logger.warning("[CameraPreview._run] 预览采集失败 error=%r", error)
                await asyncio.sleep(1.0)
                continue
            await asyncio.sleep(self._interval_s)
        logger.info("[CameraPreview._run] 无人请求预览，停止采集")

    async def close(self) -> None:
        """停止采集循环；相机由应用关闭."""
        self._closed = True
        if self._worker is not None:
            _ = self._worker.cancel()
            _ = await asyncio.gather(self._worker, return_exceptions=True)
