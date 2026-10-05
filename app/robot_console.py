# app/robot_console.py
"""赛题主入口：文字任务控制台，设备与感知采用显式模拟装配."""

import asyncio
import logging
import os
import sys

from app.robot_application import RobotApplication
from perception.simulated import SimulatedPerception
from providers.local_model import build_local_model
from robot.factory import RobotAdapterFactory
from storage.evidence import EvidenceStore


async def run_console() -> None:
    """模型和动作运行期间持续接收停止命令，普通请求串行处理."""
    robot = RobotAdapterFactory().create(os.environ.get("ROBOT_ADAPTER", "simulated"))
    app = RobotApplication(
        robot, SimulatedPerception(EvidenceStore()), build_local_model("AGENT")
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

    async def show_reply() -> None:
        """显示一次任务结果，异常显式呈现，取消不输出迟到回答."""
        try:
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
        print(
            "直接输入任务；/stop 停止当前任务，/quit 退出。每条请求创建独立任务。",
            flush=True,
        )
        while True:
            line = await lines.get()
            if not line or line.strip() == "/quit":
                break
            text = line.strip()
            if text == "/stop":
                if task_id is not None:
                    state = await app.host.cancel(task_id)
                    print("停止结果：", state.status.value, flush=True)
                continue
            if not text:
                continue
            if worker is not None and not worker.done():
                print("当前任务处理中，可输入 /stop 停止后再提交。", flush=True)
                continue
            task = await app.create_task(text)
            task_id = task.task_id
            _ = app.gateway.submit_text(task_id, text)
            worker = asyncio.create_task(show_reply(), name="robot-console-request")
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
