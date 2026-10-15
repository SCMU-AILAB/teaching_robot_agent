# tools/eval_asr.py
"""离线识别评测：对一批 WAV 与参考文本运行多个引擎，统计字错率与耗时."""

import argparse
import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from domain.validation import nonempty_string


class EvalEngine(Protocol):
    """评测脚本对识别引擎的最小要求，与常驻引擎结构兼容."""

    @property
    def name(self) -> str:
        """引擎名称，用于结果分组."""
        ...

    def check_files(self) -> None:
        """确认引擎所需的模型文件存在."""
        ...

    def load(self) -> None:
        """加载模型，评测开始前调用一次."""
        ...

    def recognize(self, content: bytes, /) -> tuple[str, str]:
        """识别 WAV 字节，返回文本与语言."""
        ...

    def unload(self) -> None:
        """释放模型资源."""
        ...


@dataclass(frozen=True)
class Reference:
    """一条评测语料的文件名、类别与参考文本."""

    filename: str
    category: str
    text: str


@dataclass(frozen=True)
class Sample:
    """一次识别的评测样本."""

    filename: str
    category: str
    reference: str
    hypothesis: str
    cer: float
    latency_ms: float


def normalize_text(text: str) -> str:
    """归一化文本：仅保留字母数字并整体小写，去除中英文标点与空白."""
    return "".join(character for character in text.casefold() if character.isalnum())


def character_error_rate(reference: str, hypothesis: str) -> float:
    """计算归一化后的字错率，编辑距离除以参考长度."""
    expected = normalize_text(reference)
    actual = normalize_text(hypothesis)
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for expected_index, expected_character in enumerate(expected, start=1):
        current = [expected_index]
        for actual_index, actual_character in enumerate(actual, start=1):
            substitute = previous[actual_index - 1] + (
                expected_character != actual_character
            )
            current.append(
                min(
                    previous[actual_index] + 1,
                    current[actual_index - 1] + 1,
                    substitute,
                )
            )
        previous = current
    return previous[-1] / len(expected)


def load_references(path: Path) -> list[Reference]:
    """读取评测语料 CSV，表头必须包含 filename、category、text."""
    references: list[Reference] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not {
            "filename",
            "category",
            "text",
        } <= set(reader.fieldnames):
            raise ValueError("参考文本 CSV 表头必须包含 filename, category, text")
        for row in reader:
            filename = nonempty_string(row.get("filename") or "", "filename")
            text = row.get("text") or ""
            references.append(
                Reference(
                    filename=filename,
                    category=nonempty_string(row.get("category") or "", "category"),
                    text=text,
                )
            )
    if not references:
        raise ValueError("参考文本 CSV 至少需要一行语料")
    return references


def build_engines(
    names: str, sensevoice_model: str, whisper_model: str, language: str
) -> list[EvalEngine]:
    """按名称构造评测引擎；引擎模块在此处才导入，构造时未加载模型."""
    engines: list[EvalEngine] = []
    for name in [item.strip() for item in names.split(",") if item.strip()]:
        if name == "sensevoice":
            from providers.asr.sensevoice_engine import SenseVoiceEngine

            engines.append(SenseVoiceEngine(sensevoice_model, language))
        elif name == "whisper":
            from providers.asr.whisper_engine import WhisperEngine

            if not whisper_model.strip():
                raise ValueError("使用 whisper 引擎必须提供模型目录")
            engines.append(WhisperEngine(whisper_model, language, "cpu", "int8"))
        else:
            raise ValueError(f"未知评测引擎：{name}")
    return engines


def evaluate(
    engine: EvalEngine, references: list[Reference], audio_dir: Path
) -> list[Sample]:
    """对全部语料依次识别并记录字错率与耗时."""
    engine.check_files()
    engine.load()
    try:
        samples: list[Sample] = []
        for reference in references:
            content = (audio_dir / reference.filename).read_bytes()
            started = time.perf_counter()
            text, _language = engine.recognize(content)
            elapsed_ms = (time.perf_counter() - started) * 1000
            samples.append(
                Sample(
                    filename=reference.filename,
                    category=reference.category,
                    reference=reference.text,
                    hypothesis=text,
                    cer=character_error_rate(reference.text, text),
                    latency_ms=elapsed_ms,
                )
            )
        return samples
    finally:
        engine.unload()


def summarize(samples: list[Sample]) -> dict[str, float]:
    """汇总总字数、总体字错率与平均、最大耗时."""
    if not samples:
        raise ValueError("没有可汇总的评测样本")
    total_characters = sum(len(normalize_text(sample.reference)) for sample in samples)
    edits = sum(
        round(sample.cer * len(normalize_text(sample.reference))) for sample in samples
    )
    return {
        "count": len(samples),
        "characters": total_characters,
        "cer": edits / total_characters if total_characters else 0.0,
        "latency_mean_ms": sum(sample.latency_ms for sample in samples) / len(samples),
        "latency_max_ms": max(sample.latency_ms for sample in samples),
    }


def render_markdown(engine_name: str, samples: list[Sample]) -> str:
    """渲染单个引擎的评测结果为 Markdown 表格."""
    summary = summarize(samples)
    lines = [
        f"## 引擎 {engine_name}",
        "",
        "| 文件 | 类别 | 参考文本 | 识别文本 | 字错率 | 耗时 ms |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for sample in samples:
        lines.append(
            f"| {sample.filename} | {sample.category} | {sample.reference} | {sample.hypothesis} | {sample.cer:.3f} | {sample.latency_ms:.0f} |"
        )
    lines.extend(
        [
            "",
            f"语料 {summary['count']:.0f} 条，参考 {summary['characters']:.0f} 字，总体字错率 {summary['cer']:.3f}，平均耗时 {summary['latency_mean_ms']:.0f} ms，最大耗时 {summary['latency_max_ms']:.0f} ms。",
        ]
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class EvalArguments:
    """评测命令行参数，字段已收敛为具体类型."""

    references: str
    audio_dir: str
    engines: str
    sensevoice_model: str
    whisper_model: str
    language: str
    output: str


def parse_arguments() -> EvalArguments:
    """解析命令行参数并收敛为具体类型."""
    parser = argparse.ArgumentParser(description="离线识别评测：字错率与耗时统计")
    _ = parser.add_argument("--references", required=True, help="参考文本 CSV 路径")
    _ = parser.add_argument("--audio-dir", required=True, help="录音 WAV 目录")
    _ = parser.add_argument(
        "--engines", default="sensevoice", help="逗号分隔的引擎名称"
    )
    _ = parser.add_argument(
        "--sensevoice-model",
        default="models/sensevoice-small",
        help="SenseVoice 模型目录",
    )
    _ = parser.add_argument(
        "--whisper-model",
        default="",
        help="faster-whisper 模型目录，使用 whisper 时必填",
    )
    _ = parser.add_argument("--language", default="zh", help="识别语言")
    _ = parser.add_argument("--output", default="", help="结果 Markdown 输出路径")
    values = parser.parse_args()
    return EvalArguments(
        references=cast(str, values.references),
        audio_dir=cast(str, values.audio_dir),
        engines=cast(str, values.engines),
        sensevoice_model=cast(str, values.sensevoice_model),
        whisper_model=cast(str, values.whisper_model),
        language=cast(str, values.language),
        output=cast(str, values.output),
    )


def main() -> None:
    """评测入口：解析参数、构造引擎、逐文件评测并输出结果."""
    # ========== Step1: 解析参数并读取语料 ==========
    arguments = parse_arguments()
    audio_dir = Path(nonempty_string(arguments.audio_dir, "audio_dir"))
    if not audio_dir.is_dir():
        raise ValueError(f"录音目录不存在：{audio_dir}")
    references = load_references(Path(arguments.references))

    # ========== Step2: 构造并加载引擎 ==========
    engines = build_engines(
        arguments.engines,
        arguments.sensevoice_model,
        arguments.whisper_model,
        arguments.language,
    )

    # ========== Step3: 逐引擎评测 ==========
    reports: list[str] = []
    for engine in engines:
        print(f"开始评测引擎 {engine.name}", flush=True)
        samples = evaluate(engine, references, audio_dir)
        reports.append(render_markdown(engine.name, samples))

    # ========== Step4: 输出结果 ==========
    document = "\n\n".join(reports) + "\n"
    print(document, end="")
    if arguments.output.strip():
        _ = Path(arguments.output).write_text(document, encoding="utf-8")


if __name__ == "__main__":
    main()
