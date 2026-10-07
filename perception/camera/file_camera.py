# perception/camera/file_camera.py
"""文件相机：从本地图片产出一帧，供无硬件环境使用."""

import asyncio
import io
import os
from typing import override

from PIL import Image, UnidentifiedImageError

from perception.camera.base import BaseCamera, RawFrame
from storage.evidence import EvidenceStore


def read_image_size(_data: bytes) -> tuple[int, int, str]:
    """读取照片尺寸和格式.

    Args:
        _data: 完整图片字节，首版支持 PNG 与 JPEG.

    Returns:
        宽、高与媒体类型三元组.

    Raises:
        ValueError: 数据不完整、格式不支持或宽高非法时抛出.
    """
    if not _data or len(_data) > 32 * 1024 * 1024:
        raise ValueError("图片为空或超过 32 MiB")
    try:
        with Image.open(io.BytesIO(_data)) as image:
            media_type = {"PNG": "image/png", "JPEG": "image/jpeg"}.get(
                image.format or ""
            )
            if media_type is None or image.width * image.height > 16_000_000:
                raise ValueError("图片格式不支持或超过 1600 万像素")
            image.verify()
        with Image.open(io.BytesIO(_data)) as image:
            _ = image.load()
            return image.width, image.height, media_type
    except (
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as error:
        raise ValueError("图片损坏或无法完整解码") from error


class FileCamera(BaseCamera):
    """在无硬件的情况下直接从本地文件读取一帧照片."""

    def __init__(
        self, _evidence: EvidenceStore, _camera_id: str, _image_path: str
    ) -> None:
        """记录图片来源、相机标识与证据存储."""
        super().__init__(_evidence, _camera_id)
        self._image_path: str = _image_path

    @override
    async def open_device(self) -> None:
        """检查图片文件是否存在."""
        if not os.path.isfile(self._image_path):
            raise ValueError("图片文件不存在/文件路径错误")

    @override
    async def grab_frame(self) -> RawFrame:
        """读文件并解析宽高与媒体类型."""
        return await asyncio.to_thread(self._read_frame)

    def _read_frame(self) -> RawFrame:
        """有界读取并解码，不阻塞事件循环."""
        with open(self._image_path, "rb") as f:
            data = f.read(32 * 1024 * 1024 + 1)
        width, height, media_type = read_image_size(data)
        return RawFrame(data=data, width=width, height=height, media_type=media_type)

    @override
    async def release_device(self) -> None:
        """文件相机没有真资源要释放."""
