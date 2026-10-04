# providers/local_model.py
"""按环境配置创建本地 Ollama 模型，导入时不连接服务."""

import os
from urllib.parse import urlsplit

from langchain_ollama import ChatOllama

from domain.validation import nonempty_string


def build_local_model(role: str = "VLM") -> ChatOllama:
    """创建本地模型客户端；部署地址必须显式配置.

    Args:
        role: VLM 或 AGENT，分别读取模型名称环境变量。

    Returns:
        有请求超时、有限上下文且绕过环境代理的客户端。

    Raises:
        ValueError: 地址缺失、地址包含凭据或角色不合法。
    """
    if role not in {"VLM", "AGENT"}:
        raise ValueError("Unknown model role")
    address = nonempty_string(os.environ.get("OLLAMA_BASE_URL"), "OLLAMA_BASE_URL")
    parsed = urlsplit(address)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Expected an HTTP origin without credentials")
    model = nonempty_string(
        os.environ.get(f"{role}_MODEL", "qwen3.5:9b"), f"{role}_MODEL"
    )
    return ChatOllama(
        model=model,
        base_url=address,
        temperature=0,
        reasoning=False,
        format="json" if role == "VLM" else "",
        num_ctx=4096,
        num_predict=768,
        keep_alive="5m",
        client_kwargs={"trust_env": False, "timeout": 60.0},
    )
