# tools/bench_tts.py
"""离线中文语音合成方案对比：合成耗时、首块延迟与实时率."""

import argparse
import statistics
import subprocess
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from domain.validation import nonempty_string

# 覆盖控制指令、短问答与教学回答三类真实长度，避免只用单句得出片面结论.
BENCH_TEXTS: dict[str, str] = {
    "短句(2字)": "停下",
    "中句(7字)": "热水壶能碰吗？",
    "长句(27字)": "水壶刚烧开的时候不要碰，先关掉电源，等凉一点再倒水。",
    "回答(28字)": "插座不能用手碰，也不能把金属东西插进去，要找大人帮忙。",
}

# 合成函数返回（整句耗时秒, 首块耗时秒, 音频时长秒）.
Synthesizer = Callable[[str], tuple[float, float, float]]


class _SherpaAudio(Protocol):
    """sherpa-onnx 合成结果中实际使用的字段."""

    @property
    def samples(self) -> Sequence[float]:
        """音频采样点序列."""
        ...

    @property
    def sample_rate(self) -> int:
        """采样率，用于把采样点数换算为时长."""
        ...


class _SherpaTts(Protocol):
    """sherpa-onnx 离线合成器中实际使用的调用边界."""

    def generate(
        self,
        text: str,
        *,
        sid: int = 0,
        speed: float = 1.0,
        callback: Callable[[object, float], int] | None = None,
    ) -> _SherpaAudio:
        """合成整段文本，callback 在分块就绪时被调用."""
        ...


@dataclass(frozen=True)
class Timing:
    """单句合成的时间测量结果."""

    label: str
    total_s: float
    first_s: float
    duration_s: float

    @property
    def rtf(self) -> float:
        """实时率：合成耗时除以音频时长，越小越快."""
        return self.total_s / self.duration_s if self.duration_s else 0.0


def measure(synthesizer: Synthesizer, repeat: int) -> list[Timing]:
    """对全部文本档位重复合成，每档取中位数."""
    if repeat < 1:
        raise ValueError("repeat 必须为正整数")
    timings: list[Timing] = []
    for label, text in BENCH_TEXTS.items():
        totals: list[float] = []
        firsts: list[float] = []
        durations: list[float] = []
        for _ in range(repeat):
            total_s, first_s, duration_s = synthesizer(text)
            totals.append(total_s)
            firsts.append(first_s)
            durations.append(duration_s)
        timings.append(
            Timing(
                label=label,
                total_s=statistics.median(totals),
                first_s=statistics.median(firsts),
                duration_s=statistics.median(durations),
            )
        )
    return timings


def build_sherpa(model_directory: str) -> Synthesizer:
    """构造 sherpa-onnx 中文 VITS 合成器，模型只加载一次."""
    base = Path(nonempty_string(model_directory, "sherpa_model_directory"))
    required = (
        base / "vits-zh-hf-fanchen-C.onnx",
        base / "lexicon.txt",
        base / "tokens.txt",
        base / "dict",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ValueError(f"sherpa-onnx 模型文件缺失：{', '.join(missing)}")

    # sherpa-onnx 无类型存根，按实际调用边界收窄，避免整条链路退化为 Unknown.
    module = import_module("sherpa_onnx")
    vits_class = cast(Callable[..., object], vars(module)["OfflineTtsVitsModelConfig"])
    model_class = cast(Callable[..., object], vars(module)["OfflineTtsModelConfig"])
    config_class = cast(Callable[..., object], vars(module)["OfflineTtsConfig"])
    tts_class = cast(Callable[[object], _SherpaTts], vars(module)["OfflineTts"])

    vits = vits_class(
        model=str(base / "vits-zh-hf-fanchen-C.onnx"),
        lexicon=str(base / "lexicon.txt"),
        tokens=str(base / "tokens.txt"),
        dict_dir=str(base / "dict"),
    )
    model = model_class(vits=vits, num_threads=4)
    config = config_class(model=model, max_num_sentences=1)
    engine = tts_class(config)

    def synthesize(text: str) -> tuple[float, float, float]:
        """返回整句耗时、首块耗时与音频时长."""
        marks: list[float] = []

        def on_chunk(_samples: object, _progress: float) -> int:
            """记录回调时刻并让生成继续.

            sherpa-onnx 1.13.8 实测语义与文档相反：返回 0 会中断生成，
            返回非零才继续。此处固定返回 1，避免只得到首个分块。
            """
            marks.append(time.perf_counter())
            return 1

        started = time.perf_counter()
        audio = engine.generate(text, sid=0, speed=1.0, callback=on_chunk)
        total_s = time.perf_counter() - started
        first_s = marks[0] - started if marks else total_s
        return total_s, first_s, len(audio.samples) / audio.sample_rate

    return synthesize


def build_piper(model_path: str) -> Synthesizer:
    """构造 Piper 进程内常驻合成器."""
    path = Path(nonempty_string(model_path, "piper_model"))
    if not path.is_file():
        raise ValueError(f"Piper 模型不存在：{path}")
    from piper import PiperVoice

    voice = PiperVoice.load(str(path))

    def synthesize(text: str) -> tuple[float, float, float]:
        """逐块累计样本数，记录首块到达时刻."""
        sample_count = 0
        started = time.perf_counter()
        first_s = 0.0
        for index, chunk in enumerate(voice.synthesize(text)):
            if index == 0:
                first_s = time.perf_counter() - started
            sample_count += len(chunk.audio_int16_bytes) // 2
        total_s = time.perf_counter() - started
        return total_s, first_s, sample_count / 16000

    return synthesize


def build_say(voice: str) -> Synthesizer:
    """构造系统 say 子进程合成器，每次调用都启动新进程."""
    voice_name = nonempty_string(voice, "say_voice")
    target = Path("/tmp/bench_say.wav")

    def synthesize(text: str) -> tuple[float, float, float]:
        """子进程合成后读取 WAV 计算音频时长."""
        started = time.perf_counter()
        _ = subprocess.run(
            [
                "say",
                "-v",
                voice_name,
                "-o",
                str(target),
                "--data-format=LEI16@16000",
                text,
            ],
            check=True,
            capture_output=True,
        )
        total_s = time.perf_counter() - started
        with wave.open(str(target), "rb") as handle:
            duration_s = handle.getnframes() / handle.getframerate()
        # 子进程模式没有增量输出，首块即整句完成时刻.
        return total_s, total_s, duration_s

    return synthesize


def render_markdown(load_s: float, engine_name: str, timings: list[Timing]) -> str:
    """渲染单方案结果表."""
    lines = [
        f"## {engine_name}",
        "",
        f"模型加载：{load_s * 1000:.0f} ms",
        "",
        "| 文本档位 | 合成耗时 ms | 首块 ms | 音频 s | 实时率 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for timing in timings:
        total_ms = timing.total_s * 1000
        first_ms = timing.first_s * 1000
        lines.append(
            f"| {timing.label} | {total_ms:.0f} | {first_ms:.0f} | {timing.duration_s:.2f} | {timing.rtf:.2f} |"
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class BenchArguments:
    """命令行参数，字段已收敛为具体类型."""

    engines: str
    sherpa_model: str
    piper_model: str
    say_voice: str
    repeat: int
    output: str


def parse_arguments() -> BenchArguments:
    """解析命令行参数并收敛为具体类型."""
    parser = argparse.ArgumentParser(description="中文离线语音合成方案对比")
    _ = parser.add_argument(
        "--engines", default="sherpa,piper,say", help="逗号分隔的方案名称"
    )
    _ = parser.add_argument(
        "--sherpa-model", default="", help="sherpa-onnx 中文 VITS 模型目录"
    )
    _ = parser.add_argument("--piper-model", default="", help="Piper 模型文件路径")
    _ = parser.add_argument("--say-voice", default="Tingting", help="系统 say 音色")
    _ = parser.add_argument("--repeat", default="3", help="每档文本重复次数")
    _ = parser.add_argument("--output", default="", help="结果 Markdown 输出路径")
    values = parser.parse_args()
    repeat_text = cast(str, values.repeat)
    try:
        repeat = int(repeat_text)
    except ValueError as error:
        raise ValueError("repeat 必须为整数") from error
    if repeat < 1:
        raise ValueError("repeat 必须为正整数")
    return BenchArguments(
        engines=cast(str, values.engines),
        sherpa_model=cast(str, values.sherpa_model),
        piper_model=cast(str, values.piper_model),
        say_voice=cast(str, values.say_voice),
        repeat=repeat,
        output=cast(str, values.output),
    )


def main() -> None:
    """逐方案加载模型并输出对比表."""
    # ========== Step1: 解析参数 ==========
    arguments = parse_arguments()
    names = [item.strip() for item in arguments.engines.split(",") if item.strip()]
    if not names:
        raise ValueError("至少需要指定一个方案")

    # ========== Step2: 逐方案测量 ==========
    reports: list[str] = []
    for name in names:
        started = time.perf_counter()
        if name == "sherpa":
            synthesizer = build_sherpa(arguments.sherpa_model)
        elif name == "piper":
            synthesizer = build_piper(arguments.piper_model)
        elif name == "say":
            synthesizer = build_say(arguments.say_voice)
        else:
            raise ValueError(f"未知合成方案：{name}")
        load_s = time.perf_counter() - started
        timings = measure(synthesizer, arguments.repeat)
        reports.append(render_markdown(load_s, name, timings))

    # ========== Step3: 输出结果 ==========
    document = "\n\n".join(reports) + "\n"
    print(document, end="")
    if arguments.output.strip():
        _ = Path(arguments.output).write_text(document, encoding="utf-8")


if __name__ == "__main__":
    main()
