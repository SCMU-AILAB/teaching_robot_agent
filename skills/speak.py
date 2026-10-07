# skills/speak.py
"""由 Runtime 管理的播报动作，实际播放完成才可判定成功."""

from typing import override
from uuid import uuid4

from domain.models import SkillResult
from domain.services import PlaybackStatus
from domain.validation import nonempty_string, string_key_dict
from robot.base import RobotAdapter
from skills.base import RobotSkill
from speech.output import SpeechOutputService


class SpeakSkill(RobotSkill):
    """串行 Runtime 独占的播报技能，语音设备由输出服务管理."""

    name: str = "speak"
    parameter_help: str = 'arguments={"text": 非空文本（最多4000字符）, "voice_id": 可选音色}；实际播完才成功。'

    def __init__(self, _output: SpeechOutputService, *, _simulated: bool) -> None:
        """注入输出服务并显式声明语音是否模拟，不从机器人类型推断."""
        self._output: SpeechOutputService = _output
        self._simulated: bool = _simulated
        self._playback_id: str | None = None

    @override
    def validate(self, args: dict[str, object]) -> None:
        """拒绝未知字段及非法文本和音色."""
        values = string_key_dict(args, "arguments")
        if values.keys() - {"text", "voice_id"}:
            raise ValueError("Unknown speech arguments")
        text = nonempty_string(values.get("text"), "text")
        if len(text) > 4000:
            raise ValueError("Speech text exceeds 4000 characters")
        if values.get("voice_id") is not None:
            _ = nonempty_string(values["voice_id"], "voice_id")

    @override
    async def execute(
        self, robot: RobotAdapter, args: dict[str, object]
    ) -> SkillResult:
        """合成与播放由同一 Runtime 执行期限覆盖，保存实际播放证据."""
        self.validate(args)
        text = nonempty_string(args.get("text"), "text")
        voice = args.get("voice_id")
        voice_id = nonempty_string(voice, "voice_id") if voice is not None else None
        playback_id = uuid4().hex
        await self._output.start(playback_id, text, voice_id)
        self._playback_id = playback_id
        state = await self._output.wait_finished(playback_id)
        if state.status != PlaybackStatus.COMPLETED:
            raise RuntimeError(f"Playback did not complete: {state.status.value}")
        return SkillResult(
            "语音已播放完成",
            {
                "playback_id": playback_id,
                "playback_status": state.status.value,
                "started_at": state.started_at,
                "ended_at": state.ended_at,
                "simulated": self._simulated,
            },
        )

    @override
    async def verify(self, robot: RobotAdapter, result: SkillResult) -> bool:
        """读取播放器实际终态，合成成功或仅开始播放都不能通过验证."""
        identifier = result.evidence.get("playback_id")
        if identifier != self._playback_id or not isinstance(identifier, str):
            return False
        state = await self._output.get_playback_state(identifier)
        return state.status == PlaybackStatus.COMPLETED and state.ended_at is not None

    @override
    async def cleanup(self, robot: RobotAdapter) -> None:
        """等待播放停止确认，并保留原有机器人停止检查."""
        try:
            if self._playback_id is not None:
                _ = await self._output.stop(self._playback_id)
        finally:
            await super().cleanup(robot)
        self._playback_id = None
