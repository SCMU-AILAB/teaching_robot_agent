# app/perception_demo.py
"""感知演示入口：按环境变量选相机，接本地 Qwen VLM 打印一次观察."""

import asyncio
import logging
import os
import sys
from uuid import uuid4

from domain.services import ObservationRequest
from domain.validation import nonempty_string
from perception.camera import CameraFactory, FileCamera, UsbCamera
from perception.perception_service import LivePerception
from perception.scene_store import SceneStore
from providers.local_model import build_local_model
from providers.ollama_vision import OllamaVisionProvider
from storage.evidence import EvidenceStore

_CAMERA_ID = "demo-camera"


async def run_once(question: str) -> None:
    """装配一次完整链路，观察一次并打印结论与证据编号.

    Args:
        question: 要问视觉模型的完整问题.
    """
    evidence = EvidenceStore()
    model = build_local_model("VLM")
    model_id = model.model
    provider = OllamaVisionProvider(model, model_id, evidence)

    factory = CameraFactory()
    name = nonempty_string(os.environ.get("PERCEPTION_CAMERA"), "PERCEPTION_CAMERA")
    if name == "file":
        path = nonempty_string(os.environ.get("PERCEPTION_IMAGE"), "PERCEPTION_IMAGE")
        factory.register("file", lambda: FileCamera(evidence, _CAMERA_ID, path))
    elif name == "usb":
        raw_index = os.environ.get("PERCEPTION_DEVICE", "0")
        try:
            index = int(raw_index)
        except ValueError as error:
            raise ValueError("PERCEPTION_DEVICE must be an integer") from error
        factory.register("usb", lambda: UsbCamera(evidence, _CAMERA_ID, index))
    else:
        raise ValueError("未知相机来源")

    camera = factory.create(name)
    service = LivePerception(camera, provider, SceneStore(), _CAMERA_ID, model_id)
    try:
        await camera.connect()
        result = await service.observe(
            ObservationRequest(uuid4().hex, 1, question, timeout_s=120.0)
        )
        origin = "缓存" if result.from_cache else "本次采集"
        print(
            f"相机：{name} 模型：{result.analysis.model_id} 模拟：{result.analysis.simulated}"
        )
        print(f"观察：{result.analysis.summary}")
        print(f"证据编号：{result.frame.evidence_id}")
        print(f"帧：{result.frame.frame_id} {result.frame.width}x{result.frame.height}")
        print(
            f"采集时刻：{result.frame.captured_at} 来源：{origin} 过期：{result.stale}"
        )
    finally:
        await service.close()


async def run_app() -> None:
    """解析命令行参数并运行一次感知演示."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    args = sys.argv[1:]
    if len(args) != 1:
        raise SystemExit("用法：python -m app.perception_demo 问题")
    await run_once(args[0])


if __name__ == "__main__":
    asyncio.run(run_app())