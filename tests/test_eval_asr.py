# tests/test_eval_asr.py
"""离线识别评测工具的纯逻辑测试，不加载真实模型."""

import tempfile
import unittest
from pathlib import Path

from tools.eval_asr import (
    Reference,
    build_engines,
    character_error_rate,
    evaluate,
    load_references,
    normalize_text,
    render_markdown,
    summarize,
)


class _FakeEngine:
    """按调用顺序返回固定识别结果的测试替身."""

    _answers: list[str]
    loaded: bool
    unloaded: bool

    def __init__(self, answers: list[str]) -> None:
        """保存按序返回的识别文本队列."""
        self._answers = answers
        self.loaded = False
        self.unloaded = False

    @property
    def name(self) -> str:
        """引擎名称."""
        return "fake"

    def check_files(self) -> None:
        """测试替身无需检查文件."""

    def load(self) -> None:
        """记录加载状态."""
        self.loaded = True

    def recognize(self, _content: bytes) -> tuple[str, str]:
        """按调用顺序弹出一个识别结果."""
        return self._answers.pop(0), "zh"

    def unload(self) -> None:
        """记录释放状态."""
        self.unloaded = True


class EvalAsrTests(unittest.TestCase):
    """字错率、语料读取与评测流程的回归测试."""

    def test_normalize_text_removes_punctuation_and_case(self) -> None:
        """标点、空白与大小写不参与字错率统计."""
        self.assertEqual(
            normalize_text(" 热水壶，能碰吗？ Hello! "), "热水壶能碰吗hello"
        )

    def test_normalize_text_unifies_digit_forms(self) -> None:
        """半角、全角阿拉伯数字与中文数字归一到同一书写形式."""
        self.assertEqual(normalize_text("第5课"), "第五课")
        self.assertEqual(normalize_text("８４消毒液"), "八四消毒液")
        self.assertEqual(normalize_text("早上９点"), "早上九点")

    def test_cer_ignores_digit_notation(self) -> None:
        """识别服务的数字规范化不再被判为错误."""
        self.assertEqual(
            character_error_rate(
                "八四消毒液和洁厕灵能混着用吗", "84消毒液和洁厕灵能混着用吗"
            ),
            0.0,
        )
        self.assertEqual(character_error_rate("第五课", "第5课"), 0.0)

    def test_cer_basic_cases(self) -> None:
        """完全一致为零，替换按比例计，空参考与非空识别按 1 处理."""
        self.assertEqual(character_error_rate("热水壶能碰吗", "热水壶能碰吗"), 0.0)
        self.assertEqual(character_error_rate("abcd", "abxd"), 0.25)
        self.assertEqual(character_error_rate("", ""), 0.0)
        self.assertEqual(character_error_rate("", "词"), 1.0)

    def test_load_references_parses_csv(self) -> None:
        """读取合法 CSV 并校验表头."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "references.csv"
            _ = path.write_text(
                "filename,category,text\na.wav,热水壶,热水壶能碰吗\nb.wav,控制指令,停下\n",
                encoding="utf-8",
            )
            references = load_references(path)
        self.assertEqual(
            references,
            [
                Reference("a.wav", "热水壶", "热水壶能碰吗"),
                Reference("b.wav", "控制指令", "停下"),
            ],
        )

    def test_load_references_rejects_missing_column(self) -> None:
        """缺少 text 列时明确报错."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "references.csv"
            _ = path.write_text("filename,category\na.wav,热水壶\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                _ = load_references(path)

    def test_evaluate_and_summarize(self) -> None:
        """逐文件评测并汇总总体字错率与耗时，引擎正确加载释放."""
        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            _ = (audio_dir / "a.wav").write_bytes(b"RIFFa")
            _ = (audio_dir / "b.wav").write_bytes(b"RIFFb")
            references = [
                Reference("a.wav", "热水壶", "热水壶能碰吗"),
                Reference("b.wav", "控制指令", "停下"),
            ]
            engine = _FakeEngine(["热水壶能碰吗", "听下"])
            samples = evaluate(engine, references, audio_dir)
            self.assertTrue(engine.loaded)
            self.assertTrue(engine.unloaded)
            self.assertEqual(
                [sample.hypothesis for sample in samples], ["热水壶能碰吗", "听下"]
            )
            self.assertEqual(samples[0].cer, 0.0)
            self.assertAlmostEqual(samples[1].cer, 1.0 / 2.0)
            summary = summarize(samples)
            self.assertEqual(summary["count"], 2)
            self.assertEqual(summary["characters"], 8)
            self.assertAlmostEqual(summary["cer"], 1.0 / 8.0)
            self.assertGreaterEqual(summary["latency_mean_ms"], 0.0)

    def test_render_markdown_contains_summary(self) -> None:
        """渲染结果包含引擎名与总体字错率."""
        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            _ = (audio_dir / "a.wav").write_bytes(b"RIFFa")
            samples = evaluate(
                _FakeEngine(["热水壶能碰吗"]),
                [Reference("a.wav", "热水壶", "热水壶能碰吗")],
                audio_dir,
            )
        document = render_markdown("fake", samples)
        self.assertIn("## 引擎 fake", document)
        self.assertIn("总体字错率 0.000", document)

    def test_build_engines_rejects_unknown_name(self) -> None:
        """未知引擎名称明确报错."""
        with self.assertRaises(ValueError):
            _ = build_engines("mock", "models/x", "", "zh")
