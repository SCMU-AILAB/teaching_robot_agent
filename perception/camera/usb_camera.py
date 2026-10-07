# perception/camera/usb_camera.py
"""通用摄像头的调用实现."""

import asyncio
from typing import cast, override

import cv2

from perception.camera.base import BaseCamera, RawFrame
from storage.evidence import EvidenceStore


def encode_frame(_frame: cv2.typing.MatLike) -> RawFrame:
    """把像素矩阵编码成 JPEG 原始数据."""
    height, width = cast(tuple[int, int, int], _frame.shape)[:2]
    ok, buffer = cv2.imencode(".jpg", _frame)

    if not ok:
        raise ValueError("未成功编码成jpg")

    data = buffer.tobytes()

    return RawFrame(data=data, height=height, width=width, media_type="image/jpeg")


class UsbCamera(BaseCamera):
    """通用摄像头的类."""

    def __init__(
        self, _evidence: EvidenceStore, _camera_id: str, _device_index: int = 0
    ) -> None:
        """记录证据存储、相机标识与设备号."""
        super().__init__(_evidence, _camera_id)
        self._device_index: int = _device_index
        self._cap: cv2.VideoCapture | None = None

    @override
    async def open_device(self) -> None:
        """在线程中打开摄像头，打不开就明确报错."""
        cap = await asyncio.to_thread(cv2.VideoCapture, self._device_index)
        if not cap.isOpened():
            await asyncio.to_thread(cap.release)
            raise ValueError("摄像头设备无法打开")
        self._cap = cap

    @override
    async def grab_frame(self) -> RawFrame:
        """在线程中控制摄像头拍照，拍照失败抛出异常."""
        if self._cap is None:
            raise RuntimeError("摄像头未打开无法拍照")
        ok, frame = await asyncio.to_thread(self._cap.read)
        if not ok:
            raise ValueError("摄像头拍摄照片失败")
        return encode_frame(frame)

    @override
    async def release_device(self) -> None:
        """有摄像头连接就释放摄像头，没有就直接返回."""
        if self._cap is None:
            return
        await asyncio.to_thread(self._cap.release)
        self._cap = None
