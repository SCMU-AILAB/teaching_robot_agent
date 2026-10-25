# tests/test_speakable_text.py
"""播报前文本清理：Markdown 标记、表情与换行不进入合成."""

import unittest

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from app.audio import build_simulated_audio
from app.robot_application import RobotApplication
from domain.models import ActionStatus
from perception.simulated import SimulatedPerception
from robot.simulated import SimulatedAdapter
from speech.text import speakable_text
from storage.evidence import EvidenceStore

# 2026-10-25 真实 qwen3.5:9b 联调时的回答原文。
_MODEL_REPLY = """小朋友，我看到桌子上的东西啦！

桌子上有三个东西：
- 左边有一个绿色的杯子
- 中间有一本蓝色的书
- 右边有一个橙色的盒子

这些都是我们平时学习或喝水会用到的东西呢！"""


class SpeakableTextTests(unittest.TestCase):
    """纯函数清理规则."""

    def test_list_reply_becomes_one_spoken_paragraph(self) -> None:
        """列表项之间补停顿，列表符号与空行去掉."""
        self.assertEqual(
            speakable_text(_MODEL_REPLY),
            "小朋友，我看到桌子上的东西啦！桌子上有三个东西："
            + "左边有一个绿色的杯子，中间有一本蓝色的书，右边有一个橙色的盒子，"
            + "这些都是我们平时学习或喝水会用到的东西呢！",
        )

    def test_markdown_emoji_links_and_code_are_removed(self) -> None:
        """标题、加粗、编号、引用、链接、代码块与表情都不朗读."""
        text = (
            "## 安全提醒 🔥\n1. **不要**碰插座\n2）把剪刀放回`抽屉`\n"
            + "> 来源：[安全手册](https://example.com)\n```python\nprint(1)\n```\n好的👍"
        )
        self.assertEqual(
            speakable_text(text),
            "安全提醒，不要碰插座，把剪刀放回抽屉，来源：安全手册，好的",
        )

    def test_plain_text_and_negative_numbers_are_kept(self) -> None:
        """纯文本原样保留；行首的负数或数字不是列表就不删."""
        self.assertEqual(speakable_text("水温大约 -5 度。"), "水温大约 -5 度。")
        self.assertEqual(speakable_text("2026年10月"), "2026年10月")
        self.assertEqual(speakable_text("3.5 米外有台阶。"), "3.5 米外有台阶。")
        self.assertEqual(speakable_text("🙂\n  \n"), "")


class SpeakSkillCleanupTests(unittest.IsolatedAsyncioTestCase):
    """播报动作把清理后的文本交给合成，清理后为空则失败."""

    async def test_speak_uses_cleaned_text_and_rejects_unreadable(self) -> None:
        """证据记录实际朗读的文本；只有表情的回答不进入播放."""
        evidence = EvidenceStore()
        app = RobotApplication(
            SimulatedAdapter(),
            SimulatedPerception(evidence),
            FakeMessagesListChatModel(responses=[]),
            _audio=build_simulated_audio(evidence),
            _evidence=evidence,
        )
        await app.start()
        try:
            task = app.gateway.create_task("播报测试")
            spoken = await app.gateway.submit_action(
                task.task_id, "speak", {"text": "- **你好**\n- 再见"}
            )
            done = await app.gateway.wait_for_action(task.task_id, spoken.action_id)
            self.assertEqual(done.status, ActionStatus.SUCCEEDED, done.failure)
            assert done.result is not None
            self.assertEqual(done.result.evidence["spoken_text"], "你好，再见")
            empty = await app.gateway.submit_action(
                task.task_id, "speak", {"text": "👍"}
            )
            failed = await app.gateway.wait_for_action(task.task_id, empty.action_id)
            self.assertEqual(failed.status, ActionStatus.FAILED)
        finally:
            await app.close()


if __name__ == "__main__":
    _ = unittest.main()
