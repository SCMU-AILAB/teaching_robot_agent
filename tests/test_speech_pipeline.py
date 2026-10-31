# tests/test_speech_pipeline.py
"""分句流水线播报：切句规则、边播边合成、汇总终态、停止与失败时的清理."""

import asyncio
import unittest
from typing import override

from domain.services import AudioReference, PlaybackState, PlaybackStatus
from providers.audio.player import MockAudioPlayer
from providers.tts.mock import MockTTSProvider
from speech.output import SpeechOutputService, split_for_speech
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore

LONG = "我看到桌子上有三个东西。左边是一个绿色的杯子。中间是一本书。右边是一个橙色的盒子，记得不要乱碰哦。"


class LoggingTTS:
    """记录合成调用顺序；指定句段可阻塞或失败."""

    def __init__(
        self,
        _inner: MockTTSProvider,
        _log: list[str],
        *,
        _block_call: int | None = None,
        _fail_call: int | None = None,
    ) -> None:
        """包装模拟合成."""
        self.inner: MockTTSProvider = _inner
        self.log: list[str] = _log
        self.block_call: int | None = _block_call
        self.fail_call: int | None = _fail_call
        self.calls: int = 0
        self.blocked: asyncio.Event = asyncio.Event()

    async def synthesize(
        self, text: str, voice_id: str | None, timeout_s: float
    ) -> AudioReference:
        """记录开始合成的句段."""
        self.calls += 1
        call = self.calls
        self.log.append(f"合成{call}")
        if call == self.fail_call:
            raise RuntimeError("synthesis failed")
        if call == self.block_call:
            self.blocked.set()
            _ = await asyncio.Event().wait()
        return await self.inner.synthesize(text, voice_id, timeout_s)


class LoggingPlayer(MockAudioPlayer):
    """记录句段播放开始与结束."""

    def __init__(self, _evidence: EvidenceStore, _log: list[str]) -> None:
        """包装模拟播放器."""
        super().__init__(_evidence, AudioDeviceLease())
        self.log: list[str] = _log
        self.ids: list[str] = []

    @override
    async def play(self, playback_id: str, audio: AudioReference) -> None:
        """记录播放开始."""
        self.ids.append(playback_id)
        self.log.append(f"播放{len(self.ids)}")
        await super().play(playback_id, audio)

    @override
    async def wait_finished(self, playback_id: str) -> PlaybackState:
        """记录播放结束."""
        state = await super().wait_finished(playback_id)
        self.log.append(f"播完{self.ids.index(playback_id) + 1}")
        return state


class SplitTests(unittest.TestCase):
    """切句规则."""

    def test_short_text_stays_single_segment(self) -> None:
        """16 字以内整段合成."""
        self.assertEqual(split_for_speech("你好。我在这里。"), ["你好。我在这里。"])

    def test_first_segment_cut_early_at_clause(self) -> None:
        """首段在第一个不短于 8 字的逗号或句末处切出；余下太短时不切."""
        self.assertEqual(
            split_for_speech("好的，我已经向左转了一点。现在能看到新东西了吗？"),
            ["好的，我已经向左转了一点。", "现在能看到新东西了吗？"],
        )
        self.assertEqual(
            split_for_speech("我看到前面有个小方桌，右边是蓝色和灰色椅子。"),
            ["我看到前面有个小方桌，", "右边是蓝色和灰色椅子。"],
        )
        self.assertEqual(
            split_for_speech("我已经向左转了一点点了，好的。"),
            ["我已经向左转了一点点了，好的。"],
        )

    def test_long_text_splits_at_sentence_ends_and_merges_short_ones(self) -> None:
        """长文本首段提前切出，其余按句末标点切开，过短的句子并入下一句，拼回原文不丢字."""
        segments = split_for_speech(LONG)
        self.assertEqual("".join(segments), LONG)
        self.assertGreater(len(segments), 1)
        self.assertGreaterEqual(len(segments[0]), 8)
        self.assertTrue(all(len(item) >= 12 for item in segments[1:-1]))
        self.assertTrue(all(item.strip() for item in segments))

    def test_long_text_without_sentence_end_stays_whole(self) -> None:
        """没有句末标点的长文本不切."""
        text = "一" * 40
        self.assertEqual(split_for_speech(text), [text])


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    """SpeechOutputService 分句播放."""

    def build(
        self, **tts_options: int | None
    ) -> tuple[SpeechOutputService, LoggingTTS, LoggingPlayer, EvidenceStore]:
        """装配带记录的合成与播放."""
        evidence = EvidenceStore()
        log: list[str] = []
        tts = LoggingTTS(
            MockTTSProvider(evidence, _duration_s=0.05),
            log,
            _block_call=tts_options.get("block"),
            _fail_call=tts_options.get("fail"),
        )
        player = LoggingPlayer(evidence, log)
        service = SpeechOutputService(tts, player, _evidence=evidence)
        self.addAsyncCleanup(service.close)
        return service, tts, player, evidence

    async def test_next_segment_synthesizes_while_current_plays(self) -> None:
        """下一句在本句播完前开始合成；汇总终态为完成，音频全部释放."""
        service, tts, player, evidence = self.build()
        segments = split_for_speech(LONG)
        await service.start("speak-1", LONG)
        state = await service.wait_finished("speak-1")
        self.assertEqual(state.status, PlaybackStatus.COMPLETED)
        self.assertEqual(state.playback_id, "speak-1")
        self.assertEqual(tts.calls, len(segments))
        self.assertEqual(player.ids[0], "speak-1")
        self.assertEqual(len(player.ids), len(segments))
        self.assertLess(tts.log.index("合成2"), tts.log.index("播完1"))
        self.assertEqual(await service.get_playback_state("speak-1"), state)
        self.assertEqual(evidence.used_bytes, 0)

    async def test_stop_while_next_segment_synthesizes(self) -> None:
        """第二句合成中停止：当前句停止确认，预取合成被取消，不再播放后续句段."""
        service, tts, player, evidence = self.build(block=2)
        await service.start("speak-1", LONG)
        async with asyncio.timeout(2):
            _ = await tts.blocked.wait()
            stopped = await service.stop("speak-1")
        self.assertIsNotNone(stopped)
        self.assertEqual(player.ids, ["speak-1"])
        with self.assertRaises(asyncio.CancelledError):
            _ = await service.wait_finished("speak-1")
        self.assertEqual(evidence.used_bytes, 0)

    async def test_later_segment_synthesis_failure_stops_output(self) -> None:
        """后续句段合成失败：输出失败，已播句段的音频释放，不假报完成."""
        service, tts, player, evidence = self.build(fail=2)
        await service.start("speak-1", LONG)
        with self.assertRaisesRegex(RuntimeError, "synthesis failed"):
            _ = await service.wait_finished("speak-1")
        self.assertEqual(service.get_stage("speak-1"), "failed")
        self.assertLessEqual(len(player.ids), 1)
        self.assertEqual(evidence.used_bytes, 0)
        self.assertEqual(tts.calls, 2)


if __name__ == "__main__":
    _ = unittest.main()
