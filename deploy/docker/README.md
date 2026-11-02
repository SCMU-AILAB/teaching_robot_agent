<!-- deploy/docker/README.md -->
# Docker 部署后端

镜像只包含后端 `app.robot_server`（Agent、HTTP/SSE、底盘、摄像头、识别），按 `uv.lock` 安装，arm64（Jetson）与 amd64 通用。大模型与 GPT-SoVITS 仍在 3080 上，不在容器里。

前提：Docker Engine 与 Compose v2.24 及以上（`docker compose version`）。

## 不接设备：模拟底盘与感知

只需要一个能访问的 Ollama（已拉取 `qwen3.5:9b`）。

```bash
cp deploy/docker/backend.env.example deploy/docker/backend.env
# 修改 OLLAMA_BASE_URL；Ollama 在宿主机上时保持 http://host.docker.internal:11434
docker compose up -d --build
curl http://127.0.0.1:8090/api/v1/system
```

前端连 `http://<这台机器的局域网地址>:8090`。

## 真车（Jetson）

1. 准备模型（不进镜像，挂载仓库 `models/`）：`models/sensevoice-small/`（model.int8.onnx、tokens.txt）与 `models/silero_vad.onnx`，下载方式见 `deploy/jetson/robot.env.example`。
2. 配置：`cp deploy/jetson/robot.env.example deploy/docker/backend.env`，按实际修改。容器内以下几项由 `compose.robot.yaml` 覆盖，不用改：`OLLAMA_BASE_URL`、`SPEECH_GPTSOVITS_URL`、`SPEECH_INPUT_DEVICE`、`SPEECH_OUTPUT_DEVICE`。
3. Jetson 能用密钥免密登录 `MODEL_SERVER_SSH`，且 `~/.ssh/known_hosts` 已有 3080（先手动 `ssh` 一次）。3080 上 `./model_server.sh start`。
4. 在宿主设置默认扬声器与麦克风（容器经宿主 PipeWire 出声，用宿主默认设备）：

   ```bash
   wpctl status                  # 找到 USB Audio Device 输出与 Earpods 输入的编号
   wpctl set-default <输出编号>; wpctl set-volume <输出编号> 1.0
   wpctl set-default <输入编号>
   ```

5. 启动（宿主上用 `robot.sh` 起过的隧道与后端须先 `./deploy/jetson/robot.sh stop`，否则端口冲突）：

   ```bash
   docker compose -f compose.yaml -f deploy/docker/compose.robot.yaml up -d --build
   docker compose -f compose.yaml -f deploy/docker/compose.robot.yaml logs -f backend
   ```

   首次启动 GPT-SoVITS 预热可能一两分钟。停止用同一组 `-f` 加 `down`；后端退出时先停车。

`compose.robot.yaml` 做的事：host 网络（隧道端口与前端直连 8090）；挂入 `/dev` 并只放开 video4linux、ttyACM、ttyUSB 三类设备（RealSense 拔插后编号变化仍可用）；挂入宿主 `pulse/native` 套接字；`tunnel` 服务用宿主 `~/.ssh`（只读）建 21435→11435、29881→9881 隧道，断线自动重连。

## 注意

- HTTP API 只在受控局域网开放，不得映射到公网。
- 不在配置文件里写密码；3080 只用 SSH 密钥。
- 音频套接字默认路径为 `/run/user/1000/pulse/native`；不是 1000 号用户时先 `export XDG_RUNTIME_DIR` 再执行 compose。
- 没有在 Jetson 上实际构建和运行过这套容器，首次上板请按 `deploy/jetson/robot.sh status` 的检查项逐一核对。
