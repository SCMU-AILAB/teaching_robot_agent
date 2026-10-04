# perception/camera/file_camera.py
"""文件相机：从本地图片产出一帧，供无硬件环境使用."""

import os
import time

from domain.services import FrameReference
from storage.evidence import EvidenceStore


def read_image_size(_data: bytes) -> tuple[int, int, str]:
    """读取照片尺寸和格式."""
    if _data.startswith(b"\x89PNG"):
        width = int.from_bytes(_data[16:20], "big")
        height = int.from_bytes(_data[20:24], "big")
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
                height = int.from_bytes(_data[_pos + 5 : _pos + 7], "big")
                width = int.from_bytes(_data[_pos + 7 : _pos + 9], "big")
                return width, height, "image/jpeg"  # ★ 按签名返回（宽, 高）

            # ---- 没有长度字段的标记：只跳 2 个字节 ----
            if marker == 0x01 or 0xD0 <= marker <= 0xD9:
                _pos += 2
                continue

            # ---- 其余标记：读出长度，整段跳过去 ----
            length = int.from_bytes(_data[_pos + 2 : _pos + 4], "big")
            if length < 2:  # 防止 _pos 不动 → 死循环
                raise ValueError("JPEG 段长度非法")
            _pos += 2 + length  # 标记2字节 + 长度字段的值

        # ========== Step3: 走到底都没找到 ==========
        raise ValueError("未找到 JPEG 尺寸信息")

    else:
        raise ValueError("传入照片格式错误")


class FileCamera:
    """在无硬件的情况下直接从本地文件读取一帧照片."""

    def __init__(
        self, _evidence: EvidenceStore, _camera_id: str, _image_path: str
    ) -> None:
        """记录图片来源、相机标识与证据存储."""
        self._evidence: EvidenceStore = _evidence
        self._camera_id: str = _camera_id
        self._image_path: str = _image_path
        self._next_frame_seq: int = 1
        self._closed: bool = False

    async def connect(self) -> None:
        """检查图片文件是否存在."""
        if not os.path.isfile(self._image_path):
            raise ValueError("图片文件不存在/文件路径错误")

    async def capture(self, scene_revision: int) -> FrameReference:
        """读文件->读宽高->取时刻->存柜子->以FrameReference返回."""
        if self._closed:
            raise RuntimeError("camera is closed")
        with open(self._image_path, "rb") as f:
            rb = f.read()
        width, height, image_type = read_image_size(rb)
        captured_at = time.time()
        receipt = await self._evidence.save(rb, image_type, captured_at)
        frame_id = f"{self._camera_id}:{self._next_frame_seq}"
        self._next_frame_seq += 1

        return FrameReference(
            frame_id=frame_id,
            camera_id=self._camera_id,
            captured_at=captured_at,
            width=width,
            height=height,
            evidence_id=receipt.evidence_id,
            scene_revision=scene_revision,
        )

    async def close(self) -> None:
        """关闭摄像头."""
        self._closed = True
