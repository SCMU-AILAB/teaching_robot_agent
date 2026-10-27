# speech/text.py
"""把模型回答整理为适合合成朗读的纯文本：去掉 Markdown 标记、表情与换行."""

import re

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# 编号列表：“1. ”须带空格，避免把行首的“3.5 米”当成编号。
_LINE_MARKER = re.compile(
    r"^\s*(?:#{1,6}\s+|>\s*|[-*+•·]\s+|\d{1,2}(?:\.\s+|[、)）]\s*))"
)
_EMPHASIS = re.compile(r"(\*\*|__|~~|\*|`)")
# 表情与杂项符号：合成引擎会读成怪声或直接报错。
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿⬀-⯿️‍⃣]")
_SPACES = re.compile(r"[ \t　]+")
_ENDINGS = "。！？!?；;，,：:、…"


def speakable_text(text: str) -> str:
    """去掉列表、标题、强调、链接、代码块和表情，把换行改为停顿.

    Args:
        text: 模型给出的最终回答.

    Returns:
        可以直接送去合成的单段文本；没有可读内容时为空字符串.
    """
    text = _LINK.sub(r"\1", _FENCE.sub("", text))
    parts: list[str] = []
    for raw in text.splitlines():
        line = _SPACES.sub(
            " ", _EMOJI.sub("", _EMPHASIS.sub("", _LINE_MARKER.sub("", raw)))
        )
        line = line.strip()
        if not line:
            continue
        if parts and parts[-1][-1] not in _ENDINGS:
            # 列表项之间原本靠换行分隔，朗读时补一个停顿。
            parts[-1] += "，"
        parts.append(line)
    return "".join(parts)
