# tests/test_bench_tts.py
"""语音合成对比工具的纯逻辑测试，不加载真实模型."""

import unittest

from tools.bench_tts import (
    BENCH_TEXTS,
    Measurement,
    Timing,
    measure,
    render_markdown,
    samples_to_seconds,
)


class BenchTtsTests(unittest.TestCase):
    """采样率换算、计时聚合与结果渲染的回归测试."""

    def test_samples_to_seconds_uses_actual_sample_rate(self) -> None:
        """16-bit 单声道字节数按实际采样率换算，不按固定采样率推断."""
        # 22050 Hz 一秒音频为 22050 个采样点，共 44100 字节.
        self.assertAlmostEqual(samples_to_seconds(44100, 22050), 1.0)
        # 16000 Hz 一秒音频为 32000 字节.
        self.assertAlmostEqual(samples_to_seconds(32000, 16000), 1.0)

    def test_samples_to_seconds_rejects_invalid_rate(self) -> None:
        """非正采样率明确报错，避免除零或负时长."""
        with self.assertRaises(ValueError):
            _ = samples_to_seconds(32000, 0)

    def test_rtf_is_total_over_duration(self) -> None:
        """实时率为合成耗时除以音频时长."""
        timing = Timing(
            label="短句", total_s=0.25, first_s=0.25, duration_s=1.0, chunks=1
        )
        self.assertAlmostEqual(timing.rtf, 0.25)

    def test_rtf_handles_zero_duration(self) -> None:
        """零时长音频不抛除零错误."""
        timing = Timing(label="空", total_s=0.1, first_s=0.1, duration_s=0.0, chunks=0)
        self.assertEqual(timing.rtf, 0.0)

    def test_measure_takes_median_and_reports_chunks(self) -> None:
        """每档文本取中位数，并保留分块数用于判断增量输出."""
        durations = iter(range(1, 100))
        calls: list[str] = []

        def fake_synthesize(text: str) -> Measurement:
            """返回递增耗时，便于验证中位数与块数聚合."""
            calls.append(text)
            index = next(durations)
            return Measurement(float(index), float(index), 1.0, index % 3 + 1)

        timings = measure(fake_synthesize, repeat=3)
        self.assertEqual(len(timings), len(BENCH_TEXTS))
        self.assertEqual(len(calls), len(BENCH_TEXTS) * 3)
        # 三次返回 1、2、3 时耗时中位数为 2，块数取 2、3、1 的中位数 2.
        self.assertAlmostEqual(timings[0].total_s, 2.0)
        self.assertEqual(timings[0].chunks, 2)

    def test_measure_rejects_non_positive_repeat(self) -> None:
        """重复次数必须为正整数."""
        with self.assertRaises(ValueError):
            _ = measure(lambda _text: Measurement(0.1, 0.1, 1.0, 1), repeat=0)

    def test_render_markdown_contains_engine_and_rows(self) -> None:
        """渲染结果包含引擎名、加载耗时、内存增量与分块数列."""
        timings = [
            Timing(
                label="短句(2字)",
                total_s=0.024,
                first_s=0.024,
                duration_s=1.0,
                chunks=1,
            )
        ]
        document = render_markdown(0.53, 312.0, "piper", timings)
        self.assertIn("## piper", document)
        self.assertIn("模型加载：530 ms", document)
        self.assertIn("进程峰值内存增量：312 MB", document)
        self.assertIn("分块数", document)
        self.assertIn("| 短句(2字) | 24 | 24 | 1.00 | 0.02 | 1 |", document)
