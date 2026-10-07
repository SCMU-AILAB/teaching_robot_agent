# app/robot_console.py
"""赛题主入口：文字任务控制台，设备与感知采用显式模拟装配."""

import asyncio
import logging
import os
import sys

from app.audio import build_simulated_audio
from app.robot_application import RobotApplication
from domain.models import TaskStatus
from perception.simulated import SimulatedPerception
from providers.local_model import build_local_model
from robot.factory import RobotAdapterFactory
from storage.evidence import EvidenceStore


async def run_console() -> None:
    """模型和动作运行期间持续接收停止命令，普通请求串行处理."""
    robot = RobotAdapterFactory().create(os.environ.get("ROBOT_ADAPTER", "simulated"))
    evidence = EvidenceStore()
    audio_mode = os.environ.get("ROBOT_AUDIO", "off")
    if audio_mode not in {"off", "simulated"}:
        raise ValueError("ROBOT_AUDIO must be off or simulated")
    audio = build_simulated_audio(evidence) if audio_mode == "simulated" else None
    app = RobotApplication(
        robot, SimulatedPerception(evidence), build_local_model("AGENT"), _audio=audio
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

    loop.add_reader(sys.stdin, read_line)
    try:
        await app.start()
        print("家庭学习陪伴与安全教育机器人：模型为真实本地模型，设备和感知为模拟。")
        print(f"语音模式：{audio_mode}（simulated 仅模拟播放，不输出真实声音）。")
        print(
            "直接输入或追问；/finish 确认完成，/stop 停止，/quit 退出。",
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
            if text == "/stop":
                if task_id is not None:
                    state = await app.cancel_task(task_id)
                    if worker is not None and not worker.done():
                        _ = worker.cancel()
                        _ = await asyncio.gather(worker, return_exceptions=True)
                    print("停止结果：", state.status.value, flush=True)
                continue
            if not text:
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
