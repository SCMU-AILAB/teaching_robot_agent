# tests/test_bench_tts.py
"""语音合成对比工具的纯逻辑测试，不加载真实模型."""

import unittest

from tools.bench_tts import BENCH_TEXTS, Timing, measure, render_markdown


class BenchTtsTests(unittest.TestCase):
    """计时聚合与结果渲染的回归测试."""

    def test_rtf_is_total_over_duration(self) -> None:
        """实时率为合成耗时除以音频时长."""
        timing = Timing(label="短句", total_s=0.25, first_s=0.25, duration_s=1.0)
        self.assertAlmostEqual(timing.rtf, 0.25)

    def test_rtf_handles_zero_duration(self) -> None:
        """零时长音频不抛除零错误."""
        timing = Timing(label="空", total_s=0.1, first_s=0.1, duration_s=0.0)
        self.assertEqual(timing.rtf, 0.0)

    def test_measure_takes_median_and_covers_all_texts(self) -> None:
        """每档文本取重复测量中位数，档位数与语料一致."""
        durations = iter(range(1, 100))
        calls: list[str] = []

        def fake_synthesize(text: str) -> tuple[float, float, float]:
            """返回递增耗时，便于验证中位数选择."""
            calls.append(text)
            index = next(durations)
            return float(index), float(index), 1.0

        timings = measure(fake_synthesize, repeat=3)
        self.assertEqual(len(timings), len(BENCH_TEXTS))
        self.assertEqual(len(calls), len(BENCH_TEXTS) * 3)
        # 三次返回 1、2、3 时中位数为 2.
        self.assertAlmostEqual(timings[0].total_s, 2.0)

    def test_measure_rejects_non_positive_repeat(self) -> None:
        """重复次数必须为正整数."""
        with self.assertRaises(ValueError):
            _ = measure(lambda _text: (0.1, 0.1, 1.0), repeat=0)

    def test_render_markdown_contains_engine_and_rows(self) -> None:
        """渲染结果包含引擎名、加载耗时与每个文本档位."""
        timings = [
            Timing(label="短句(2字)", total_s=0.024, first_s=0.024, duration_s=1.0)
        ]
        document = render_markdown(0.53, "piper", timings)
        self.assertIn("## piper", document)
        self.assertIn("模型加载：530 ms", document)
        self.assertIn("| 短句(2字) | 24 | 24 | 1.00 | 0.02 |", document)
