# perception/camera/file_camera.py
"""文件相机：从本地图片产出一帧，供无硬件环境使用."""

import os
from typing import override

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
    if _data.startswith(b"\x89PNG"):
        # 宽高固定落在 [16:24]；长度不够就是截断（切片越界不报错，只给空串）
        if len(_data) < 24:
            raise ValueError("PNG 数据不完整")
        width = int.from_bytes(_data[16:20], "big")
        height = int.from_bytes(_data[20:24], "big")
        if width <= 0 or height <= 0:
            raise ValueError("PNG 宽高不合法")
        return width, height, "image/png"

    elif _data.startswith(b"\xff\xd8"):
        # ========== Step1: 从开头之后开始走 ==========
        _pos = 2  # 跳过 FF D8（JPEG 的签名）

        # ========== Step2: 一段一段往后跳，直到找到 SOF ==========
        while _pos < len(_data):
            # 要读 _pos+1，先确认它还在范围内（否则下面会越界）
            if _pos + 1 >= len(_data):
                raise ValueError("JPEG 数据不完整")

            # 每一段的开头必须都是 FF，不是就说明数据坏了
            if _data[_pos] != 0xFF:
                raise ValueError("JPEG 数据损坏")

            marker = _data[_pos + 1]

            # ---- 找到 SOF（含尺寸的那一段）----
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                # 段内结构：[0]FF [1]标记 [2:4]长度 [4]精度 [5:7]高 [7:9]宽
                if _pos + 9 > len(_data):
                    raise ValueError("JPEG 数据不完整")
                height = int.from_bytes(_data[_pos + 5 : _pos + 7], "big")
                width = int.from_bytes(_data[_pos + 7 : _pos + 9], "big")
                if width <= 0 or height <= 0:
                    raise ValueError("JPEG 宽高不合法")
                return width, height, "image/jpeg"  # ★ 按签名返回（宽, 高）

            # ---- 没有长度字段的标记：只跳 2 个字节 ----
            if marker == 0x01 or 0xD0 <= marker <= 0xD9:
                _pos += 2
                continue

            # ---- 其余标记：读出长度，整段跳过去 ----
            length = int.from_bytes(_data[_pos + 2 : _pos + 4], "big")
            if length < 2:  # 防止 _pos 不动 → 死循环
                raise ValueError("JPEG 段长度非法")
            if _pos + 2 + length > len(_data):  # 段比文件还长 → 数据被截断
                raise ValueError("JPEG 数据不完整")
            _pos += 2 + length  # 标记2字节 + 长度字段的值

        # ========== Step3: 走到底都没找到 ==========
        raise ValueError("未找到 JPEG 尺寸信息")

    else:
        raise ValueError("传入照片格式错误")


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
        with open(self._image_path, "rb") as f:
            data = f.read()
        width, height, media_type = read_image_size(data)
        return RawFrame(data=data, width=width, height=height, media_type=media_type)

    @override
    async def release_device(self) -> None:
        """文件相机没有真资源要释放."""
