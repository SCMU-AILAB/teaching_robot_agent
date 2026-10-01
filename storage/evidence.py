# storage/evidence.py
"""供感知与语音共同使用的有容量限制的内存证据存储."""

import time
from uuid import uuid4

from domain.services import EvidenceReference
from domain.validation import finite_float, positive_int


class EvidenceStore:
    """按不透明编号读写媒体；首版不自动清理正在引用的证据."""

    def __init__(self, _max_bytes: int = 64 * 1024 * 1024) -> None:
        """配置总容量，存满后拒绝写入而不是隐式删除证据."""
        self._max_bytes: int = positive_int(_max_bytes, "max_bytes")
        self._used_bytes: int = 0
        self._entries: dict[str, tuple[EvidenceReference, bytes]] = {}

    async def save(
        self, content: bytes, media_type: str, captured_at: float
    ) -> EvidenceReference:
        """保存媒体并返回引用，超出容量或不支持的格式抛出异常."""
        if media_type not in {"image/jpeg", "image/png", "audio/wav"}:
            raise ValueError("Unsupported media type")
        timestamp = finite_float(captured_at, "captured_at")
        if timestamp < 0 or not content:
            raise ValueError("Expected non-empty content and nonnegative timestamp")
        if self._used_bytes + len(content) > self._max_bytes:
            raise ValueError("Evidence storage capacity exceeded")
        reference = EvidenceReference(
            uuid4().hex, media_type, time.time(), timestamp, len(content)
        )
        self._entries[reference.evidence_id] = (reference, bytes(content))
        self._used_bytes += len(content)
        return reference

    def get(self, evidence_id: str) -> EvidenceReference:
        """按登记编号查询元数据，未知编号抛出 KeyError."""
        return self._entries[evidence_id][0]

    async def read(self, evidence_id: str) -> bytes:
        """按登记编号读取媒体，不接受文件系统路径."""
        return self._entries[evidence_id][1]
