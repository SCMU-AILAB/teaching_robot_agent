# app/robot_console.py
"""赛题主入口：文字与语音任务控制台，设备与感知采用显式装配."""

import asyncio
import logging
import os
import sys
from uuid import uuid4

from app.audio import AudioAdapters, build_real_audio, build_simulated_audio
from app.perception import build_live_perception, build_simulated_perception
from app.robot_application import RobotApplication
from domain.models import TaskStatus
from providers.local_model import build_local_model
from robot.factory import RobotAdapterFactory
from storage.evidence import EvidenceStore


async def run_console() -> None:
    """模型和动作运行期间持续接收停止命令，普通请求串行处理."""
    robot = RobotAdapterFactory().create(os.environ.get("ROBOT_ADAPTER", "simulated"))
    evidence = EvidenceStore()
    audio_mode = os.environ.get("ROBOT_AUDIO", "off")
    if audio_mode not in {"off", "simulated", "real"}:
        raise ValueError("ROBOT_AUDIO must be off, simulated or real")
    audio: AudioAdapters | None = None
    if audio_mode == "simulated":
        audio = build_simulated_audio(evidence)
    elif audio_mode == "real":
        audio = build_real_audio(evidence)
    mode = os.environ.get("ROBOT_PERCEPTION", "simulated")
    if mode not in {"simulated", "live"}:
        raise ValueError("ROBOT_PERCEPTION must be simulated or live")
    if mode == "simulated":
        perception = build_simulated_perception(evidence)
    else:
        perception = build_live_perception(evidence)
    if perception.camera is not None:
        await perception.camera.connect()

    app = RobotApplication(
        robot,
        perception.service,
        build_local_model("AGENT"),
        _audio=audio,
        _evidence=evidence,
    )
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue[str] = asyncio.Queue()

    def read_line() -> None:
        """事件驱动读取终端，不留下阻塞 input 线程."""
        line = sys.stdin.readline()
        lines.put_nowait(line)
        if not line:
            _ = loop.remove_reader(sys.stdin)

    worker: asyncio.Task[None] | None = None
    task_id: int | None = None
    recording_id: str | None = None

    async def show_reply(expected_task_id: int) -> None:
        """显示一次任务结果，异常显式呈现，取消不输出迟到回答."""
        try:
            reply = await app.process_next()
            while reply.rejected and reply.task_id != expected_task_id:
                reply = await app.process_next()
            print(
                f"机器人：{reply.text}\n任务状态：{reply.task_status.value}", flush=True
            )
        except asyncio.CancelledError:
            print("本次处理已取消。", flush=True)
        except Exception as error:
            print(
                f"任务失败：{type(error).__name__}；请检查服务与运行日志。", flush=True
            )

    async def transcribe_and_reply(identifier: str, expected_task_id: int) -> None:
        """结束录音并等待识别，非空转写由服务入队后显示机器人回答."""
        try:
            item = await app.finish_recording(identifier)
        except asyncio.CancelledError:
            print("本次录音已取消。", flush=True)
            return
        except Exception as error:
            print(
                f"语音识别失败：{type(error).__name__}；请检查模型与设备。", flush=True
            )
            return
        if item is None:
            print("没有识别到语音，请重新 /record。", flush=True)
            return
        print(f"你（语音）：{item.text}", flush=True)
        await show_reply(expected_task_id)

    async def active_task() -> int:
        """沿用未结束的任务，否则为本次输入创建新任务."""
        nonlocal task_id
        if task_id is None or (await app.gateway.get_snapshot(task_id)).task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            task_id = (await app.create_task("语音或文字对话")).task_id
        return task_id

    loop.add_reader(sys.stdin, read_line)
    try:
        await app.start()
        print(
            "家庭学习陪伴与安全教育机器人：模型为真实本地模型，"
            + f"设备：{os.environ.get('ROBOT_ADAPTER', 'simulated')}，"
            + f"感知：{'模拟' if perception.simulated else '真实相机'}。"
        )
        print(
            f"语音模式：{audio_mode}（simulated 仅模拟播放；real 使用本机麦克风、"
            + "常驻识别模型和扬声器）。"
        )
        print(
            "直接输入或追问；/record 开始录音，/done 结束录音并识别；"
            + "/finish 确认完成，/stop 停止，/quit 退出。",
            flush=True,
        )
        while True:
            line = await lines.get()
            if not line or line.strip() == "/quit":
                break
            text = line.strip()
            if text == "/finish":
                if task_id is not None:
                    if worker is not None and not worker.done():
                        print("请等待当前请求结束再确认完成。", flush=True)
                    else:
                        try:
                            state = await app.finish_task(task_id)
                            print("验收结果：", state.status.value, flush=True)
                            task_id = None
                        except (ValueError, RuntimeError) as error:
                            print(f"无法验收：{error}", flush=True)
                continue
            if text == "/record":
                if audio is None:
                    print(
                        "未启用语音：设置 ROBOT_AUDIO=real 或 simulated。", flush=True
                    )
                elif recording_id is not None or (
                    worker is not None and not worker.done()
                ):
                    print("正在录音或处理中，请先 /done 或 /stop。", flush=True)
                else:
                    identifier = uuid4().hex
                    try:
                        await app.start_recording(
                            await active_task(),
                            identifier,
                            audio.max_duration_s,
                            audio.timeout_s,
                        )
                    except (ValueError, RuntimeError) as error:
                        print(f"无法开始录音：{error}", flush=True)
                    else:
                        recording_id = identifier
                        print(
                            f"正在录音（最长 {audio.max_duration_s:.0f} 秒），说完输入 /done。",
                            flush=True,
                        )
                continue
            if text == "/done":
                if recording_id is None or task_id is None:
                    print("当前没有录音，先输入 /record。", flush=True)
                else:
                    worker = asyncio.create_task(
                        transcribe_and_reply(recording_id, task_id),
                        name="robot-console-speech",
                    )
                    recording_id = None
                continue
            if text == "/stop":
                recording_id = None
                if task_id is not None:
                    state = await app.cancel_task(task_id)
                    if worker is not None and not worker.done():
                        _ = worker.cancel()
                        _ = await asyncio.gather(worker, return_exceptions=True)
                    print("停止结果：", state.status.value, flush=True)
                continue
            if not text:
                continue
            if recording_id is not None:
                print("正在录音，请先 /done 结束录音。", flush=True)
                continue
            if worker is not None and not worker.done():
                print("当前任务处理中，可输入 /stop 停止后再提交。", flush=True)
                continue
            if task_id is None or (
                await app.gateway.get_snapshot(task_id)
            ).task.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                task = await app.create_task(text)
                task_id = task.task_id
            _ = app.gateway.submit_text(task_id, text)
            worker = asyncio.create_task(
                show_reply(task_id), name="robot-console-request"
            )
    finally:
        _ = loop.remove_reader(sys.stdin)
        if worker is not None:
            _ = worker.cancel()
            _ = await asyncio.gather(worker, return_exceptions=True)
        await app.close()


def run_app() -> None:
    """主入口配置日志并运行交互控制台，支持 macOS 与 Linux 终端."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    asyncio.run(run_console())


if __name__ == "__main__":
    run_app()
