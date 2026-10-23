# app/robot_server.py
"""机器人后端主进程：装配设备、感知、语音与 Agent，对外提供 API_CONTRACT.md v1.0."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn

from api.http import ServiceSlot, build_http_app
from api.preview import CameraPreview
from api.service import RobotApiService, ServiceStates
from app.audio import AudioAdapters, build_real_audio, build_simulated_audio
from app.perception import build_perception
from app.robot_application import RobotApplication
from providers.local_model import build_local_model
from robot.factory import RobotAdapterFactory
from storage.evidence import EvidenceStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def serve_robot(slot: ServiceSlot) -> AsyncGenerator[None]:
    """按环境变量装配并启动机器人，退出时先停 API 服务再关闭设备.

    Raises:
        ValueError: 环境变量取值未知或缺少必填配置.
    """
    adapter_name = os.environ.get("ROBOT_ADAPTER", "simulated")
    robot = RobotAdapterFactory().create(adapter_name)
    evidence = EvidenceStore()
    audio_mode = os.environ.get("ROBOT_AUDIO", "off")
    if audio_mode not in {"off", "simulated", "real"}:
        raise ValueError("ROBOT_AUDIO must be off, simulated or real")
    audio: AudioAdapters | None = None
    if audio_mode == "simulated":
        audio = build_simulated_audio(evidence)
    elif audio_mode == "real":
        audio = build_real_audio(evidence)
    perception = build_perception(evidence)
    app = RobotApplication(
        robot,
        perception.service,
        build_local_model("AGENT"),
        _audio=audio,
        _evidence=evidence,
    )
    preview = (
        CameraPreview(perception.camera, evidence)
        if perception.camera is not None
        else None
    )
    speech_state = "not_configured" if audio is None else "ready"
    service = RobotApiService(
        app,
        _services=ServiceStates(
            agent="ready",
            vlm="not_configured" if perception.simulated else "ready",
            asr=speech_state,
            tts=speech_state,
        ),
        _simulated=robot.is_simulated or perception.simulated,
        _preview=preview,
        _evidence=evidence,
        _camera_error="未配置摄像头（模拟感知）",
    )
    try:
        if perception.camera is not None:
            # 在 try 内连接：之后任何失败都经 app.close() 关闭感知服务并释放相机。
            await perception.camera.connect()
        await app.start()
        await service.start()
        slot.service = service
        logger.info(
            "[serve_robot] 机器人后端已启动 adapter=%s audio=%s perception=%s",
            adapter_name,
            audio_mode,
            "simulated" if perception.simulated else "live",
        )
        yield
    finally:
        try:
            await service.close()
        finally:
            await app.close()


def run_server() -> None:
    """主入口：默认只监听本机；局域网部署显式设置 ROBOT_API_HOST."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    host = os.environ.get("ROBOT_API_HOST", "127.0.0.1")
    port = int(os.environ.get("ROBOT_API_PORT", "8000"))
    uvicorn.run(
        build_http_app(ServiceSlot(), lifespan=serve_robot),
        host=host,
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "INFO").lower(),
        # SSE 连接不会自行结束，退出时最多等待 3 秒再关闭。
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    run_server()
