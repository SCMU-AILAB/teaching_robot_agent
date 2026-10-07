# speech/demo.py
"""演示共享设备租约的内部模拟链路：uv run python -m speech.demo."""

import asyncio
from contextlib import AsyncExitStack

from app.team_gateway import TeamGateway
from domain.services import PlaybackStatus, TranscriptResult
from perception.simulated import SimulatedPerception
from providers.asr.mock import MockASRProvider
from providers.audio.mock import MockAudioRecorder
from providers.audio.player import MockAudioPlayer
from providers.tts.mock import MockTTSProvider
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.registry import SkillRegistry
from speech.input import SpeechInputService
from speech.output import SpeechOutputService
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


async def run_demo() -> None:
    """验证内部输入与输出装配，不调用教学 Agent 或真实设备.

    Raises:
        RuntimeError: 输入交接、播放终态或资源释放不符合预期。
        TimeoutError: 模拟链路未能在期限内完成。
    """
    # ========== Step1: 同一装配上下文共享证据存储和设备租约 ==========
    evidence = EvidenceStore()
    device = AudioDeviceLease()
    perception = SimulatedPerception(evidence)
    recorder = MockAudioRecorder(evidence, device)
    player = MockAudioPlayer(evidence, device)
    asr = MockASRProvider(_result=TranscriptResult("这是杯子"))
    tts = MockTTSProvider(evidence)
    async with AsyncExitStack() as stack:
        _ = stack.push_async_callback(perception.close)
        runtime = await stack.enter_async_context(
            ActionManager(SimulatedAdapter(), SkillRegistry())
        )
        gateway = TeamGateway(runtime, perception)
        input_service = SpeechInputService(recorder, asr, gateway)
        output_service = SpeechOutputService(tts, player)
        _ = stack.push_async_callback(output_service.close)
        _ = stack.push_async_callback(input_service.close)
        async with asyncio.timeout(10):
            # ========== Step2: 最终转写通过核心门面进入唯一输入队列 ==========
            task = gateway.create_task("内部模拟语音联调")
            await input_service.start(task.task_id, "demo-recording", 15)
            submitted = await input_service.finish("demo-recording")
            received = await gateway.next_input()
            if submitted is None or received != submitted:
                raise RuntimeError("Speech input handoff failed")
            print(f"模拟识别已进入 UserInput：{received.text}")

            # ========== Step3: 调用方提供固定回复，不伪装教学决策 ==========
            reply = "这是固定测试回答，不是教学 Agent 生成的回答。"
            await output_service.start("demo-playback", reply)
            playback = await output_service.wait_finished("demo-playback")
            if playback.status != PlaybackStatus.COMPLETED:
                raise RuntimeError("Simulated playback did not complete")
            print("固定测试回答已完成模拟播放；没有真实人声或扬声器输出。")

            # ========== Step4: 显式取消演示任务，语音资源另由上下文关闭 ==========
            _ = await gateway.cancel_task(task.task_id)
    if device.busy:
        raise RuntimeError("Audio lease not released")
    print(
        f"录音完成 {recorder.finalize_count} 次，ASR {asr.call_count} 次，"
        + f"TTS {tts.call_count} 次，播放 {player.play_count} 次；资源已释放。"
    )


if __name__ == "__main__":
    asyncio.run(run_demo())
