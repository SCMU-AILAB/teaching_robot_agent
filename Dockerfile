# Dockerfile
# 机器人后端镜像（app.robot_server）：依赖按 uv.lock 安装，arm64（Jetson）与 amd64 通用。
# 模型权重不进镜像，运行时把仓库 models/ 挂到 /app/models。用法见 deploy/docker/README.md。
FROM python:3.12-slim-bookworm

# libportaudio2：录音与播放；libasound2-plugins：ALSA 经宿主 PipeWire/PulseAudio 出声；
# openssh-client：同一镜像兼作到 3080 的隧道
RUN apt-get update \
    && apt-get install -y --no-install-recommends libportaudio2 libasound2-plugins openssh-client \
    && rm -rf /var/lib/apt/lists/*
# 容器内 ALSA 默认设备走宿主音频服务（未挂载音频套接字时只是打不开声音，不影响文字对话）
RUN printf 'pcm.!default pulse\nctl.!default pulse\n' > /etc/asound.conf

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app
# 先只装依赖，源码改动不重装
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra speech --extra car --extra depth --no-cache

COPY agent ./agent
COPY api ./api
COPY app ./app
COPY domain ./domain
COPY education ./education
COPY perception ./perception
COPY providers ./providers
COPY robot ./robot
COPY runtime ./runtime
COPY skills ./skills
COPY speech ./speech
COPY storage ./storage

ENV PATH="/app/.venv/bin:$PATH" \
    ROBOT_API_HOST=0.0.0.0 \
    ROBOT_API_PORT=8090
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"ROBOT_API_PORT\"]}/api/v1/system', timeout=3)"

CMD ["python", "-m", "app.robot_server"]
