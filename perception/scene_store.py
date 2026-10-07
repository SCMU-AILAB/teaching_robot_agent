# perception/scene_store.py
"""场景观察结论的账本：按钥匙记一行，按钥匙查一行, 清除旧版本的行."""

from dataclasses import dataclass

from domain.services import FrameReference, VisionAnalysis
from domain.validation import finite_float, nonempty_string, positive_int


@dataclass(frozen=True)
class SceneRecord:
    """一次观察的账目：钥匙、那一帧、结论和两个时刻.

    Attributes:
        question: 规范化后的完整问题，首尾空白已去掉.
        camera_id: 采集这一帧的相机标识.
        model_id: 产出结论的模型标识，由配置注入.
        scene_revision: 这次观察属于第几版场景.
        captured_at: 这一帧的采集时刻，取自帧自身.
        frame: 完整的帧引用，返回给核心时原样交回.
        analysis: 经适配器校验的观察结论.
        analyzed_at: 推理完成的时刻.
    """

    question: str
    camera_id: str
    model_id: str
    scene_revision: int
    captured_at: float
    frame: FrameReference
    analysis: VisionAnalysis
    analyzed_at: float


class SceneStore:
    """按钥匙记录和查询观察结论，查不到返回 None."""

    def __init__(self, _max_entries: int = 128) -> None:
        """配置账本容量上限并初始化空账.

        Args:
            _max_entries: 账本最多保留多少行，必须为正整数.

        Raises:
            ValueError: 容量不是正整数时抛出.
        """
        self._max_entries: int = positive_int(_max_entries, "max_entries")
        self._rows: dict[tuple[str, str, str, int], SceneRecord] = {}

    async def remember(
        self,
        _question: str,
        _frame: FrameReference,
        _analysis: VisionAnalysis,
        _analyzed_at: float,
    ) -> None:
        """把一次观察记成一行，同一把钥匙只保留最新的那一行.

        Args:
            _question: 核心传来的完整问题，首尾空白会被去掉.
            _frame: 本次采集的帧引用，采集时刻从它身上取.
            _analysis: 视觉适配器返回的观察结论.
            _analyzed_at: 推理完成的时刻.

        Raises:
            ValueError: 问题、相机标识或模型标识为空文本时抛出.
        """
        question = nonempty_string(_question, "question").strip()
        camera_id = nonempty_string(_frame.camera_id, "camera_id")
        model_id = nonempty_string(_analysis.model_id, "model_id")
        analyzed_at = finite_float(_analyzed_at, "analyzed_at")
        key = (question, camera_id, model_id, _frame.scene_revision)

        existing = self._rows.get(key)
        if existing is not None and _frame.captured_at <= existing.captured_at:
            return
        if existing is None and len(self._rows) >= self._max_entries:
            oldest_key = min(self._rows, key=lambda k: self._rows[k].captured_at)
            del self._rows[oldest_key]
        self._rows[key] = SceneRecord(
            question=question,
            camera_id=camera_id,
            model_id=model_id,
            scene_revision=_frame.scene_revision,
            captured_at=_frame.captured_at,
            frame=_frame,
            analysis=_analysis,
            analyzed_at=analyzed_at,
        )

    def drop_stale(self, _current_revision: int) -> int:
        """清除版本更旧的行."""
        stale_keys = [
            key
            for key, row in self._rows.items()
            if row.scene_revision < _current_revision
        ]
        for key in stale_keys:
            del self._rows[key]
        return len(stale_keys)

    def find(
        self, _question: str, _camera_id: str, _model_id: str, _scene_revision: int
    ) -> SceneRecord | None:
        """按钥匙查一行，查不到返回 None.

        Args:
            _question: 要查的完整问题，首尾空白会被去掉.
            _camera_id: 要查的相机标识.
            _model_id: 要查的模型标识.
            _scene_revision: 要查的场景版本.

        Returns:
            命中的那一行；没有命中时返回 None.
        """
        question = nonempty_string(_question, "question").strip()
        camera_id = nonempty_string(_camera_id, "camera_id")
        model_id = nonempty_string(_model_id, "model_id")
        key = (question, camera_id, model_id, _scene_revision)
        return self._rows.get(key)
