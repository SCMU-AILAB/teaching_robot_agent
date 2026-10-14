# speech/real_demo.py
"""独立真实语音控制台，不驱动机器人或调用教学模型."""

import asyncio
import logging
import os
import sys
from contextlib import AsyncExitStack
from uuid import uuid4

from app.team_gateway import TeamGateway
from perception.simulated import SimulatedPerception
from providers.asr.remote import RemoteASRProvider
from providers.audio.real_player import PortAudioPlayer
from providers.audio.recorder import PortAudioRecorder
from providers.tts.remote import RemoteTTSProvider
from robot.simulated import SimulatedAdapter
from runtime.action_manager import ActionManager
from skills.registry import SkillRegistry
from speech.config import SpeechConfig
from speech.input import SpeechInputService
from speech.output import SpeechOutputService
from speech.remote import SSHSpeechTransport
from speech.resources import AudioDeviceLease
from storage.evidence import EvidenceStore


async def run_demo() -> None:
    """持续接收录音、结束、停止和退出命令，退出时关闭全部资源."""
    config = SpeechConfig.from_env()
    evidence = EvidenceStore()
    lease = AudioDeviceLease()
    recorder = PortAudioRecorder(
        evidence, lease, config.input_device, config.sample_rate
    )
    player = PortAudioPlayer(evidence, lease, config.output_device)
    transport = SSHSpeechTransport(config.server)
    asr = RemoteASRProvider(evidence, transport, config.language)
    tts = RemoteTTSProvider(evidence, transport, config.voice)
    perception = SimulatedPerception(evidence)
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue[str] = asyncio.Queue()

    def read_line() -> None:
        """事件驱动读取终端，不留下等待 input 的后台线程."""
        line = sys.stdin.readline()
        lines.put_nowait(line)
        if not line:
            _ = loop.remove_reader(sys.stdin)

    async with AsyncExitStack() as stack:
        # ========== Step1: 显式装配真实音频，核心仅提供输入队列 ==========
        _ = stack.push_async_callback(perception.close)
        runtime = await stack.enter_async_context(
            ActionManager(SimulatedAdapter(), SkillRegistry())
        )
        gateway = TeamGateway(runtime, perception)
        inputs = SpeechInputService(recorder, asr, gateway)
        outputs = SpeechOutputService(tts, player)
        _ = stack.push_async_callback(outputs.close)
        _ = stack.push_async_callback(inputs.close)
        task = gateway.create_task("独立真实语音验收")
        worker: asyncio.Task[None] | None = None
        finishing: asyncio.Task[None] | None = None
        recording_id: str | None = None

        async def cycle(identifier: str) -> None:
            """一次录音只提交一个最终输入，固定回答不冒充教学生成."""
            try:
                await inputs.start(
                    task.task_id, identifier, config.max_duration_s, config.timeout_s
                )
                print("正在录音；/finish 结束，/stop 丢弃。", flush=True)
                item = await inputs.wait_finished(identifier)
                if item is None:
                    print("未识别到有效语音。", flush=True)
                    return
                received = await gateway.next_input()
                if received != item:
                    raise RuntimeError("Unexpected input queue item")
                print(f"识别：{item.text}", flush=True)
                print("正在合成固定回答。", flush=True)
                await outputs.start(
                    identifier,
                    "我已经听到你的回答，这是语音链路测试。",
                    timeout_s=config.timeout_s,
                )
                state = await outputs.wait_finished(identifier)
                print(f"播放结果：{state.status.value}", flush=True)
            except asyncio.CancelledError:
                print("本轮已取消，语音资源已清理。", flush=True)
                raise
            except Exception as error:
                print(f"语音失败：{type(error).__name__}: {error}", flush=True)

        async def finish_recording(identifier: str) -> None:
            """后台等待录音结束，让终端仍能接收停止和退出命令."""
            try:
                _ = await recorder.finish(identifier)
            except (KeyError, RuntimeError, ValueError):
                print("本轮录音尚未开始或已失败。", flush=True)

        # ========== Step2: 主循环持续接收停止，不等待识别和播放完成 ==========
        loop.add_reader(sys.stdin, read_line)
        try:
            print("本地麦克风 → 服务器 ASR/TTS → 本地扬声器；固定测试回答。")
            print(
                "/record 开始，/finish 结束录音，/stop 取消本轮，/quit 退出。",
                flush=True,
            )
            while True:
                line = await lines.get()
                command = line.strip()
                if not line or command == "/quit":
                    break
                if command == "/stop":
                    if worker is not None and not worker.done():
                        _ = worker.cancel()
                        _ = await asyncio.gather(worker, return_exceptions=True)
                    continue
                if command == "/finish" and recording_id is not None:
                    if (
                        worker is not None
                        and not worker.done()
                        and (finishing is None or finishing.done())
                    ):
                        finishing = asyncio.create_task(finish_recording(recording_id))
                    continue
                if command == "/record":
                    if worker is not None and not worker.done():
                        print("本轮尚未结束，可使用 /stop。", flush=True)
                        continue
                    recording_id = uuid4().hex
                    worker = asyncio.create_task(cycle(recording_id))
                else:
                    print("命令：/record /finish /stop /quit", flush=True)
        finally:
            # ========== Step3: 取消当前轮次并等待确认，随后由上下文关闭设备 ==========
            _ = loop.remove_reader(sys.stdin)
            if worker is not None:
                _ = worker.cancel()
                _ = await asyncio.gather(worker, return_exceptions=True)
            if finishing is not None:
                _ = finishing.cancel()
                _ = await asyncio.gather(finishing, return_exceptions=True)
            _ = await gateway.cancel_task(task.task_id)


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"))
    try:
        asyncio.run(run_demo())
    except KeyboardInterrupt:
        pass
