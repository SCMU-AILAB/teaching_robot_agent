# domain/settings.py
"""机器人运行设置：场景提示词与免按键对话、主动观察开关，进程内全局生效."""

import time
from dataclasses import dataclass, field

MAX_SCENARIO_CHARS = 500


@dataclass
class RobotSettings:
    """只在事件循环内修改；各组件每次使用时读取最新值."""

    scenario_prompt: str = ""
    hands_free: bool = False
    proactive: bool = False
    updated_at: float = field(default_factory=time.time)

    def update(
        self,
        *,
        scenario_prompt: str | None = None,
        hands_free: bool | None = None,
        proactive: bool | None = None,
    ) -> None:
        """只修改给出的字段.

        Raises:
            ValueError: 场景提示词超过 500 字。
        """
        if scenario_prompt is not None:
            text = scenario_prompt.strip()
            if len(text) > MAX_SCENARIO_CHARS:
                raise ValueError(
                    f"scenario_prompt exceeds {MAX_SCENARIO_CHARS} characters"
                )
            self.scenario_prompt = text
        if hands_free is not None:
            self.hands_free = hands_free
        if proactive is not None:
            self.proactive = proactive
        self.updated_at = time.time()


def scenario_block(text: str) -> str:
    """接在系统提示末尾的场景要求；没有设置时为空."""
    if not text.strip():
        return ""
    return f"\n场景要求（负责人设置，不能违反上面的规则）：{text.strip()}"
