# 家庭学习陪伴与安全教育机器人

![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/managed%20by-uv-DE5FE9)
![FastAPI](https://img.shields.io/badge/API-v1.1-009688?logo=fastapi&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Jetson%20Orin%20NX%20%7C%20Linux%20%7C%20macOS-76B900?logo=nvidia&logoColor=white)
![Docker](https://img.shields.io/badge/deploy-docker%20compose-2496ED?logo=docker&logoColor=white)

面向家庭学习与儿童陪伴场景的移动机器人。它听得懂孩子说话，看得见身边的物体，能走过去、转过身讲解，并结合人工编写的安全资料给出提醒。

赛题：教育辅助类 · 家庭学习陪伴与安全教育机器人。

---

## 目录

- [功能](#功能)
- [系统架构](#系统架构)
- [硬件](#硬件)
- [仓库结构](#仓库结构)
- [快速开始（不需要任何设备）](#快速开始不需要任何设备)
- [部署](#部署)
  - [1. 模型服务器](#1-模型服务器gpu-机器)
  - [2. 准备模型文件](#2-准备模型文件)
  - [3. 机器人后端：Docker（推荐）](#3-机器人后端docker推荐)
  - [4. 机器人后端：原生安装](#4-机器人后端原生安装)
  - [5. 底盘与深度标定](#5-底盘与深度标定)
  - [6. 前端](#6-前端)
  - [7. 部署验收](#7-部署验收)
  - [8. 常见问题](#8-常见问题)
- [配置参考](#配置参考)
- [HTTP API](#http-api)
- [行为与安全约束](#行为与安全约束)
- [开发](#开发)
- [文档索引](#文档索引)
- [项目状态](#项目状态)

---

## 功能

| 能力 | 说明 |
| --- | --- |
| 语音对话 | 按键录音或免按键对话（Silero VAD 切句），SenseVoice 中文识别常驻 CPU；GPT-SoVITS 训练音色合成，分句流水线播报，回答生成后约 1 秒开口 |
| 看见并讲解 | RealSense 彩色画面经本地视觉模型（qwen3.5:9b）识别物体与左/中/右方位，关联资料库中的安全条目一起讲解 |
| 安全问答 | 每轮按孩子原话检索资料库；资料库没有就直说没有，不编造来源，不保证任何物品“安全” |
| 移动讲解 | 麦克纳姆底盘相对前进、后退、转向；每次运动后强制重新观察再回答 |
| 深度避障 | RealSense 深度计算车身通道内的空隙，前进中每 0.1 秒检查，不够就停车 |
| 实时地图 | 车停稳时把深度扫描按里程计画成 5 厘米一格的平面地图（`GET /api/v1/map`，车载屏“地图”页）；孩子说“回来”时按地图绕开障碍走回陪伴出发点 |
| 自由探索 | “去探索一下吧”：按空隙选方向一段段走，遇到资料库里有的物体就讲一句，最后总结 |
| 主动观察 | 任务进行中且没人说话时定期观察，发现新的危险物品主动提醒 |
| 场景提示词 | 负责人可设置机器人名字、孩子称呼、场景要求，不能覆盖安全规则 |
| 统一停止 | 停止命令立即取消模型等待、动作、录音与播报；无法确认停车时锁定执行层 |

## 系统架构

```text
 ┌──────────── 前端 ────────────┐
 │ Flutter 操作终端（桌面/Web）   │      HTTP + SSE（API v1.1，局域网）
 │ robot-ui 板载触摸屏（C/Raylib）│ ───────────────────────────────┐
 └──────────────────────────────┘                                 │
                                                                  ▼
 ┌──────────────────── Jetson Orin NX：机器人后端 app.robot_server（:8090）─────────────────────┐
 │ FastAPI ─ RobotApiService ─ TeamGateway ─ RobotHost ─ EmbodiedAgent（LangChain 工具循环）    │
 │                                   │                     ├ lookup_knowledge  资料库           │
 │                                   │                     ├ observe_scene     感知服务         │
 │                                   │                     ├ submit_action     Skill Runtime    │
 │                                   │                     └ start_exploration 自由探索         │
 │ Skill Runtime ─ GuardedRobotAdapter（深度避障）─ CarAdapter ──USB 串口──▶ STM32 底盘固件     │
 │ LivePerception ── RealSense 彩色      深度守护 ── RealSense Z16                              │
 │ 语音：麦克风 → Silero VAD → SenseVoice（常驻）       回答 → 分句 → 合成 → 扬声器             │
 └─────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                           │ SSH 隧道（Jetson 本机 21435 / 29881）
                                           ▼
 ┌────────────── GPU 服务器（RTX 3080）──────────────┐
 │ Ollama  qwen3.5:9b   127.0.0.1:11435（对话+视觉）  │  只监听本机，不对局域网开放
 │ GPT-SoVITS api_v2    127.0.0.1:9881（语音合成）    │
 └────────────────────────────────────────────────────┘
```

一轮对话的处理链路：

```text
语音/文字 → 去重入队 → Agent（检索资料 + 本轮状态 + 对话记忆）→ 模型决定：
    回答 ─────────────────────────────────────────────▶ speak 动作 → 分句合成播放
    观察 / 动作 → Runtime 执行 → 核对位姿与证据 → 运动后重新观察 → 回到模型
```

模型不直接操作设备：动作经 Skill Runtime 提交，执行后按设备遥测验收，“接受”不等于“完成”。设计细节见 [赛题框架](docs/COMPETITION_ARCHITECTURE.md)。

## 硬件

| 部件 | 型号 | 用途 |
| --- | --- | --- |
| 主控 | NVIDIA Jetson Orin NX（Super 开发套件） | 后端、识别、感知、底盘控制 |
| 底盘 | 麦克纳姆四轮，MINDA-01 驱动板（STM32F407），USB 串口 `1a86:55d4` | 运动，20 Hz 编码器遥测，300 ms 看门狗 |
| 相机 | Intel RealSense D435i | 彩色画面（观察）+ 深度（避障），须接 USB 3 口 |
| 麦克风 | Apple EarPods | 语音输入 |
| 扬声器 | C-Media USB Audio Device | 语音输出 |
| 模型服务器 | RTX 3080 工作站 | Ollama 与 GPT-SoVITS |

固件源码在 `firmware/car`（Rust/embassy），协议纯逻辑与测试向量在 `firmware/car-core`。

## 仓库结构

```text
.
├── app/                 入口：robot_server（HTTP 后端）、robot_console（main.py 控制台）、标定与演示工具
├── api/                 HTTP/SSE 路由、服务层、事件流、摄像头预览
├── agent/               Agent 工具循环、宿主、对话记忆、观察简报、探索、主动观察
├── runtime/             动作生命周期、任务状态、事件广播
├── skills/              移动、转向、播报等技能（按设备能力开放）
├── robot/               设备抽象、模拟器、麦克纳姆底盘 CarAdapter、深度避障守护
├── perception/          相机、感知服务、RealSense 深度几何
├── speech/              语音输入输出服务、免按键收音、分句流水线
├── providers/           外部能力适配：Ollama、SenseVoice/Whisper、Silero VAD、GPT-SoVITS、音频设备
├── education/           安全教育资料库与课程（可选）
├── domain/ storage/     领域模型、运行设置、证据存储
├── frontend/            Flutter 操作终端
├── robot-ui/            Jetson 板载触摸屏前端（C11 + Raylib）
├── firmware/            STM32 底盘固件（Rust）
├── deploy/
│   ├── docker/          Docker 部署：真车叠加配置、配置模板
│   └── jetson/          原生部署：robot.sh 一键启停、配置模板、systemd 单元
├── tests/               unittest（450+ 项）
├── docs/                架构、部署指南、模块合同、评测与设计记录
├── Dockerfile  compose.yaml
├── model_server.sh      在 GPU 服务器上启停模型服务、开本机隧道
└── API_CONTRACT.md      全部对外接口的唯一依据
```

## 快速开始（不需要任何设备）

用模拟底盘、模拟感知、模拟播报跑通文字对话，只需要一个能访问的 Ollama（已 `ollama pull qwen3.5:9b`）。

**方式一：Docker**

```bash
git clone https://github.com/SCMU-AILAB/teaching_robot_agent.git
cd teaching_robot_agent
cp deploy/docker/backend.env.example deploy/docker/backend.env
# Ollama 在宿主机上时保持 OLLAMA_BASE_URL=http://host.docker.internal:11434，否则改成实际地址
docker compose up -d --build
curl http://127.0.0.1:8090/api/v1/system
```

**方式二：本机 Python**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # 已装 uv 可跳过
uv sync --locked
export OLLAMA_BASE_URL=http://127.0.0.1:11434 ROBOT_AUDIO=simulated \
       ROBOT_API_HOST=127.0.0.1 ROBOT_API_PORT=8090
uv run python -m app.robot_server
```

然后打开 `http://127.0.0.1:8090/api/v1/docs` 试接口，或者启动[前端](#6-前端)。不想开前端时，`uv run main.py` 是终端控制台：直接输入问题，`/stop` 停止任务，`/finish` 确认完成，`/quit` 退出。

> 程序不读取 `.env` 文件，所有配置来自环境变量（Docker 由 `env_file` 注入）。

---

## 部署

正式部署分两台机器：

| 机器 | 运行 | 网络 |
| --- | --- | --- |
| GPU 服务器（3080） | Ollama、GPT-SoVITS | 只监听 `127.0.0.1`，由 Jetson 经 SSH 隧道访问 |
| Jetson（机器人上） | 后端 `app.robot_server`，可选板载 `robot-ui` | 8090 端口只对受控局域网开放 |

部署顺序：模型服务器 → 模型文件 → 后端（Docker 或原生，二选一）→ 标定 → 前端 → 验收。

### 1. 模型服务器（GPU 机器）

需要 NVIDIA GPU（显存 ≥ 10 GB，qwen3.5:9b 与 GPT-SoVITS 同时常驻）。

**Ollama**：只监听本机的 11435 端口。

```bash
# /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11435"
```

```bash
OLLAMA_HOST=127.0.0.1:11435 ollama pull qwen3.5:9b
```

**GPT-SoVITS**：以 `api_v2.py -a 127.0.0.1 -p 9881` 作为 systemd 服务 `sovits-api` 运行，并设置环境变量 `TORCH_CUDNN_V8_API_DISABLED=1`（PyTorch 2.10+/cuDNN 9 下不设会慢到一句一分钟以上）。准备一段 3–10 秒参考音频及其原文，路径与原文填进后端配置的 `SPEECH_GPTSOVITS_REF_AUDIO`、`SPEECH_GPTSOVITS_PROMPT_TEXT`。

不需要真实声音时可跳过 GPT-SoVITS：后端设 `ROBOT_AUDIO=simulated`（模拟播报）或 `SPEECH_TTS_ENGINE=espeak-ng`（Linux 系统合成）。

**启停**：服务不设开机自启，用前启动、用完释放显存。在任意能 SSH 到服务器的机器上：

```bash
export MODEL_SERVER_SSH=<用户>@<服务器地址>     # 只用 SSH 密钥，不写密码
./model_server.sh start     # 启动并等待就绪（GPT-SoVITS 冷启动可能数分钟）
./model_server.sh status    # 服务状态、显存、已加载模型
./model_server.sh stop      # 卸载模型、停止合成服务
./model_server.sh tunnel    # 开发机调试：本机 21435/29881 隧道
```

### 2. 准备模型文件

识别与语音检测模型放在仓库根目录 `models/`（已被 git 忽略，Docker 运行时挂载，不进镜像）：

```bash
mkdir -p models/sensevoice-small
HF=https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main
curl -L -o models/sensevoice-small/model.int8.onnx "$HF/model.int8.onnx"
curl -L -o models/sensevoice-small/tokens.txt      "$HF/tokens.txt"
curl -L -o models/silero_vad.onnx \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx
```

国内网络可把 `huggingface.co` 换成 `hf-mirror.com`。缺少 SenseVoice 时 `ROBOT_AUDIO=real` 启动直接失败；缺少 Silero 时只是不提供免按键对话。

### 3. 机器人后端：Docker（推荐）

镜像按 `uv.lock` 安装全部后端依赖（语音、底盘、深度），arm64 与 amd64 通用。需要 Docker Engine 与 Compose v2.24+。

**① 配置**

```bash
cp deploy/jetson/robot.env.example deploy/docker/backend.env
```

逐项核对 `deploy/docker/backend.env`：`MODEL_SERVER_SSH`、底盘标定常数、GPT-SoVITS 参考音频。下列项在容器中被 `compose.robot.yaml` 覆盖，不用改：`OLLAMA_BASE_URL`、`SPEECH_GPTSOVITS_URL`、`SPEECH_INPUT_DEVICE`、`SPEECH_OUTPUT_DEVICE`。

**② SSH 密钥**：容器内的隧道使用 Jetson 用户的 `~/.ssh`（只读）。

```bash
ssh-keygen -t ed25519                 # 已有密钥可跳过
ssh-copy-id <用户>@<服务器地址>
ssh -o BatchMode=yes <用户>@<服务器地址> echo ok    # 同时把服务器写入 known_hosts
```

**③ 宿主音频**：容器经宿主 PipeWire 发声和收音，使用宿主的默认设备。每次插拔声卡后核对一次：

```bash
wpctl status                                   # 找 USB Audio Device（输出）与 Earpods（输入）的编号
wpctl set-default <输出编号> && wpctl set-volume <输出编号> 1.0
wpctl set-default <输入编号>
```

**④ 启动**

```bash
alias robot='docker compose -f compose.yaml -f deploy/docker/compose.robot.yaml'
robot up -d --build        # 首次构建约十几分钟；GPT-SoVITS 预热需一两分钟
robot logs -f backend      # 看到“机器人后端已启动”即就绪
robot ps                   # backend 健康状态应为 healthy
robot down                 # 停止：后端先停车、释放设备
```

`compose.robot.yaml` 做了这些事：

| 项 | 做法 |
| --- | --- |
| 网络 | host 网络：后端经 `127.0.0.1:21435/29881` 访问隧道，前端直连 Jetson 的 8090 |
| 设备 | 挂入 `/dev`，cgroup 只放开 video4linux、ttyACM、ttyUSB；RealSense 与底盘重插后编号变了仍可用 |
| 音频 | 挂入 `$XDG_RUNTIME_DIR/pulse/native`（默认 `/run/user/1000`），容器内 ALSA 默认设备走宿主 PipeWire |
| 隧道 | `tunnel` 服务：`21435→11435`、`29881→9881`，断线自动重连 |
| 模型 | 只读挂载 `./models` |

> 已用 `robot.sh` 原生启动过的，先 `./deploy/jetson/robot.sh stop`，否则端口冲突。

### 4. 机器人后端：原生安装

不用 Docker 时，在 Jetson 上直接安装（系统包与用户组需要 sudo）：

```bash
git clone https://github.com/SCMU-AILAB/teaching_robot_agent.git ~/teaching_robot_agent
cd ~/teaching_robot_agent
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv sync --frozen --extra speech --extra car --extra depth
sudo apt install -y libportaudio2          # 录音与播放
sudo usermod -aG dialout "$USER"           # 底盘串口，重新登录生效

mkdir -p ~/teaching-robot
cp deploy/jetson/robot.env.example ~/teaching-robot/robot.env && chmod 600 ~/teaching-robot/robot.env
```

SSH 密钥同上一节第 ② 步。之后用一键脚本：

```bash
./deploy/jetson/robot.sh start     # 检查设备 → 设置扬声器与麦克风 → 建隧道 → 启动后端
./deploy/jetson/robot.sh status    # 隧道、后端、模型服务、设备
./deploy/jetson/robot.sh logs      # 跟踪后端日志
./deploy/jetson/robot.sh stop      # 停止后端（先停车）与隧道
```

有设备没接好时 `start` 会拒绝启动，强制启动用 `start-anyway`。原生部署的完整说明（声卡名称、音量、systemd 常驻）见 [Jetson 设备端部署指南](docs/JETSON_DEVICE_GUIDE.md)。

### 5. 底盘与深度标定

换车、换轮子或换地面后需要重新标定，需要人在车旁，且要先停后端（标定工具独占串口与相机）。下面是原生命令；Docker 部署在命令前加 `robot run --rm --no-deps backend`，例如 `robot run --rm --no-deps backend python -m app.car_calibrate /dev/ttyACM0 forward --permille 300 --seconds 2`。

| 步骤 | 命令 | 结果写入配置 |
| --- | --- | --- |
| 前进里程 | `uv run --extra car python -m app.car_calibrate /dev/ttyACM0 forward --permille 300 --seconds 2`，按提示输入实测距离 | `CAR_COUNTS_PER_METER` |
| 转向里程 | `uv run --extra car python -m app.car_calibrate /dev/ttyACM0 turn --permille 300 --seconds 2`，输入实测角度 | `CAR_COUNTS_PER_RADIAN` |
| 相机安装 | 空地上 `uv run --extra depth python -m app.depth_calibrate mount` | `DEPTH_CAMERA_HEIGHT_M`、`DEPTH_CAMERA_PITCH_DEG`，并设 `DEPTH_CAMERA=realsense` |
| 避障核对 | 前方摆纸箱，`uv run --extra depth python -m app.depth_calibrate watch --seconds 20` | — |

未标定里程时底盘只连接、不开放运动技能；`DEPTH_CAMERA=off` 时不避障、不开放自由探索。

### 6. 前端

| 前端 | 运行 | 说明 |
| --- | --- | --- |
| Flutter 操作终端 | `cd frontend && flutter pub get && flutter run -d macos`（或 `flutter build web`） | 对话、摄像头、动作记录、机器人设置；设备地址在应用内设置，见 [frontend/README.md](frontend/README.md) |
| 板载触摸屏 | 构建：`robot-ui/scripts/build_jetson.sh`；装桌面图标：`deploy/jetson/robot_screen.sh install`，之后双击桌面「机器人界面」即启动后端并全屏显示 | 车载屏，见 [robot-ui/README.md](robot-ui/README.md) |

Web 版后端没有跨源响应头，须与后端同源托管。

### 7. 部署验收

```bash
curl -s http://<Jetson>:8090/api/v1/system            # 各服务 ready，simulated=false
curl -s -o snap.jpg http://<Jetson>:8090/api/v1/camera/snapshot   # 画面正常
```

然后在前端依次验证：问“你好” → 能听到回答；“看看桌上有什么” → 讲出物体与方位；“向左转一点点” → 车转动并重新观察；说话中按停止 → 播报与动作立即停止。完整 10 项清单见 [部署指南第 6 节](docs/JETSON_DEVICE_GUIDE.md#6-验收自检清单)。

### 8. 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 消息被拒，错误码 50002 | 模型服务不可达：`model_server.sh status`，检查隧道（Docker：`robot logs tunnel`） |
| 首句播报很慢 | GPT-SoVITS 冷启动或久置后换页，后端启动时会预热；服务器内存紧张时更明显 |
| 没有声音 | 宿主默认输出被选成别的设备或音量被复原，重做部署第 ③ 步；原生部署 `robot.sh start` 会自动处理 |
| `No capture device matches` | 声卡名称不匹配：`uv run --extra speech python -m providers.audio.worker devices` 列出名称 |
| RealSense 反复断开 | 接到了 USB 2 口或扩展坞（12M 连接），换 USB 3 口 |
| 前进很短就停，原因“前方有障碍物” | 避障按速度预留停车距离，前方空间不足；空地上用 `depth_calibrate watch` 核对读数 |
| 动作失败后无法再动 | 停车无法确认时执行层锁定以保安全；检查底盘供电与串口后重启后端 |
| 隧道反复重连 | 服务器 sshd 丢弃新连接或密钥未授权：`ssh -o BatchMode=yes <服务器> echo ok` |

---

## 配置参考

完整模板：开发机 [`.env.example`](.env.example)，真车 [`deploy/jetson/robot.env.example`](deploy/jetson/robot.env.example)，Docker 模拟 [`deploy/docker/backend.env.example`](deploy/docker/backend.env.example)。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | 必填 | Ollama 地址；Jetson 上为隧道 `http://127.0.0.1:21435` |
| `AGENT_MODEL` / `VLM_MODEL` | — | 对话与视觉模型，均为 `qwen3.5:9b` |
| `OLLAMA_KEEP_ALIVE` | `30m` | 模型在显存中的常驻时长 |
| `AGENT_ANSWER_CHARS` | `40` | 回答大致字数，超长时重答；0 不限 |
| `ROBOT_API_HOST` / `ROBOT_API_PORT` | `127.0.0.1` / `8000` | 局域网部署设 `0.0.0.0` / `8090` |
| `ROBOT_ADAPTER` | `simulated` | `car` 为麦克纳姆底盘 |
| `CAR_SERIAL_PORT` | — | 底盘串口，如 `/dev/ttyACM0` |
| `CAR_COUNTS_PER_METER` / `CAR_COUNTS_PER_RADIAN` | — | 标定结果；缺少时不开放运动 |
| `CAR_MAX_PERMILLE` | `600` | 单轴速度上限千分比；真车用 `1000`（约 0.22 m/s） |
| `ROBOT_PERCEPTION` | `simulated` | `live` 为真实相机 + 视觉模型 |
| `PERCEPTION_CAMERA` / `PERCEPTION_DEVICE` | — | `usb` + `realsense`（自动找彩色节点）或 `/dev/videoN` 的 N；`file` + `PERCEPTION_IMAGE` 用静态图 |
| `DEPTH_CAMERA` | `off` | `realsense` 启用深度避障与自由探索 |
| `OBSTACLE_STOP_M` | `0.4` | 避障停车距离（另加滑行量） |
| `EXPLORE_RADIUS_M` / `EXPLORE_MAX_S` | `3` / `300` | 探索离起点半径与时长上限 |
| `ROBOT_AUDIO` | `off` | `simulated` 模拟播报；`real` 真实麦克风、识别、合成、扬声器 |
| `SPEECH_INPUT_DEVICE` / `SPEECH_OUTPUT_DEVICE` | 系统默认 | 声卡名称子串，不填序号 |
| `SPEECH_ASR_MODEL_PATH` | — | `models/sensevoice-small` |
| `SPEECH_VAD_MODEL_PATH` | — | `models/silero_vad.onnx`；不设则无免按键对话 |
| `HANDS_FREE_WAKE_WORD` | — | 设置后免按键对话须以唤醒词开头 |
| `SPEECH_TTS_ENGINE` | `espeak-ng`（macOS 为 `say`） | `gpt-sovits` 为训练音色 |
| `SPEECH_GPTSOVITS_URL` / `_REF_AUDIO` / `_PROMPT_TEXT` | — | 合成服务地址、参考音频（服务器上的路径）及其原文 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

## HTTP API

[`API_CONTRACT.md`](API_CONTRACT.md)（v1.2）是全部对外接口的唯一依据，前端路径常量在 `frontend/lib/core/constants/api_paths.dart`。接口变更须先改合同、经负责人确认再实现。运行中的后端提供交互文档 `/api/v1/docs`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/system` | 设备、模型、语音状态 |
| GET / PATCH | `/api/v1/settings` | 场景提示词、免按键对话、主动观察 |
| POST | `/api/v1/tasks` | 创建任务（同时只有一个活动任务） |
| GET / PATCH | `/api/v1/tasks/{id}` | 任务快照；停止或确认完成 |
| POST | `/api/v1/tasks/{id}/messages` | 发送文字消息 |
| POST / PATCH | `/api/v1/tasks/{id}/recordings[/{rid}]` | 开始录音；结束并识别或放弃 |
| GET | `/api/v1/tasks/{id}/events` | SSE 事件流（消息、动作、观察、状态） |
| GET | `/api/v1/camera/snapshot` | 摄像头单帧 |
| GET | `/api/v1/evidence/{id}` | 观察事件中的画面 |
| GET | `/api/v1/map` | 实时栅格地图、机器人位姿与出发点（需深度相机） |

所有响应统一为 `{code, message, data}`，不返回堆栈。

## 行为与安全约束

**机器人行为**

- 动作“已接受”不等于“已完成”：执行后核对位姿、静止状态与遥测时间才记成功；失败、超时、取消不自动重试。
- 每次运动后旧观察作废，宿主强制重新观察后再让模型继续；不能把短距离移动说成“到达某物体”。
- 回答声称“已经转了”“更近了”“很安全”而实际没有依据时，纠正重答一次；主动讲解仍违规就不说。
- 安全讲解只来自画面观察与人工编写的资料条目（待负责人审核），不是现场风险检测或安全认证。
- 单条输入失败只作废这一条，任务继续；停止确认失败时锁定执行层并结束任务。

**部署安全**

- 模型服务只监听服务器本机，经 SSH 隧道访问；不得对局域网开放。
- 后端 8090 只在受控局域网开放，不得映射到公网。
- 凭据只用 SSH 密钥和环境变量，不写进配置文件或仓库；日志不输出凭据。

## 开发

```bash
uv sync --locked --extra speech --extra car --extra depth
```

提交前必须全部通过（basedpyright 要求零错误、零警告，不得以 ignore / noqa 或降低检查级别绕过）：

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run python -m unittest discover -s tests -v
```

离线演示（不需要模型服务，`--local-model` 改用已配置的 Ollama）：

```bash
uv run python -m app.robot_audio_demo     # 语音输入 → 回答 → 播报 → 停止
uv run python -m app.demo --scenario all  # 动作生命周期
uv run python -m app.model_demo vision /path/to/photo.png '图中有什么？'   # 需要 Ollama
```

约定（详见 [编码规范](docs/CODING_SPEC.md) 与 [AGENTS.md](AGENTS.md)）：

- 每个 Python 文件首行为相对路径注释；标识符英文，注释与文档字符串中文，Google 风格。
- 显式构造函数的业务参数用前导下划线；覆写方法用 `@override`。
- 日志格式 `[ClassName.method_name] 描述`，日志配置只在入口执行。
- 模块导入时不得连接设备、启动线程或修改全局状态。
- 提交信息 `<type>(<scope>): <subject>`，如 `fix(agent): …`、`feat(deploy): …`。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [API_CONTRACT.md](API_CONTRACT.md) | HTTP API、底盘串口协议、Ollama 与 GPT-SoVITS 调用约定 |
| [docs/COMPETITION_ARCHITECTURE.md](docs/COMPETITION_ARCHITECTURE.md) | 赛题框架、模块边界、宿主流程 |
| [docs/JETSON_DEVICE_GUIDE.md](docs/JETSON_DEVICE_GUIDE.md) | Jetson 原生部署、声卡、标定、验收清单、已知问题 |
| [docs/MODULE_CONTRACT.md](docs/MODULE_CONTRACT.md) | 媒体、感知、语音接口与证据存储 |
| [docs/ROBOT_ADAPTER.md](docs/ROBOT_ADAPTER.md) | 接入新设备：实现 RobotAdapter |
| [docs/LOCAL_MODELS.md](docs/LOCAL_MODELS.md) | 本地模型选型、配置与联调结果 |
| [docs/TTS_SELECTION.md](docs/TTS_SELECTION.md) | 语音合成选型 |
| [docs/CODING_SPEC.md](docs/CODING_SPEC.md) | 编码规范 |
| [frontend/README.md](frontend/README.md) | Flutter 终端：运行、设备发现、打包 |
| [robot-ui/README.md](robot-ui/README.md) | 板载触摸屏：依赖、构建、配置 |
| [firmware/car/README.md](firmware/car/README.md) | 底盘固件、遥控与标定 |
| [AGENTS.md](AGENTS.md) | 开发约定与历次审查、联调记录 |

## 项目状态

**已在真机验证**（Jetson + 真车 + RealSense + GPT-SoVITS）：观察与物体讲解、追问、转向与前进（误差约 2 厘米内）、深度避障停车、自由探索、语音回环识别、统一停止。

**尚未验收**

- 真人说话下的免按键对话与主动观察。
- 横移标定、拔线看门狗停车。
- Docker 部署未在 Jetson 上实际构建运行。
- 前端“确认完成”入口；Web 版跨源访问。
- 安全资料条目内容待负责人审核。

运行记录（任务、动作、对话、证据）只保存在内存，进程退出后不保留；各类记录有上限，满额时清理最早的已结束记录。
