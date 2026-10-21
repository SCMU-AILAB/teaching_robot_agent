# app/perception.py
"""相机零件的组装并提供真假切换服务."""

import os
from dataclasses import dataclass

from domain.validation import nonempty_string
from perception.camera import CameraFactory, FileCamera, UsbCamera
from perception.interfaces import CameraSource, PerceptionService
from perception.perception_service import LivePerception
from perception.scene_store import SceneStore
from perception.simulated import SimulatedPerception
from providers.local_model import build_local_model
from providers.ollama_vision import OllamaVisionProvider
from storage.evidence import EvidenceStore

_CAMERA_ID = "main-camera"


@dataclass(frozen=True)
class PerceptionAdapters:
    """应用拥有的感知依赖；真实相机由应用在启动时连接."""

    service: PerceptionService
    camera: CameraSource | None
    simulated: bool


def build_simulated_perception(evidence: EvidenceStore) -> PerceptionAdapters:
    """模拟感知，无需相机或模型服务."""
    return PerceptionAdapters(
        service=SimulatedPerception(evidence),
        camera=None,
        simulated=True,
    )


def build_perception(evidence: EvidenceStore) -> PerceptionAdapters:
    """按 ROBOT_PERCEPTION 选择模拟或真实感知，未知值直接报错."""
    mode = os.environ.get("ROBOT_PERCEPTION", "simulated")
    if mode == "simulated":
        return build_simulated_perception(evidence)
    if mode == "live":
        return build_live_perception(evidence)
    raise ValueError("ROBOT_PERCEPTION must be simulated or live")


def build_live_perception(evidence: EvidenceStore) -> PerceptionAdapters:
    """按环境装配真实相机和本地 VLM，缺配置直接报错."""
    name = nonempty_string(os.environ.get("PERCEPTION_CAMERA"), "PERCEPTION_CAMERA")
    if name not in {"file", "usb"}:
        raise ValueError("PERCEPTION_CAMERA must be file or usb")

    factory = CameraFactory()
    if name == "file":
        path = nonempty_string(os.environ.get("PERCEPTION_IMAGE"), "PERCEPTION_IMAGE")
        factory.register("file", lambda: FileCamera(evidence, _CAMERA_ID, path))
    else:
        raw_index = os.environ.get("PERCEPTION_DEVICE", "0")
        try:
            index = int(raw_index)
        except ValueError as error:
            raise ValueError("PERCEPTION_DEVICE must be an integer") from error
        factory.register("usb", lambda: UsbCamera(evidence, _CAMERA_ID, index))
    camera = factory.create(name)

    model = build_local_model("VLM")
    provider = OllamaVisionProvider(model, model.model, evidence)
    liveperception = LivePerception(
        _camera=camera,
        _provider=provider,
        _store=SceneStore(),
        _evidence=evidence,
        _camera_id=_CAMERA_ID,
        _model_id=model.model,
    )
    return PerceptionAdapters(
        service=liveperception,
        camera=camera,
        simulated=False,
    )
