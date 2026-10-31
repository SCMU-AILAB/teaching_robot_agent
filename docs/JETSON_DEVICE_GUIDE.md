# Jetson 设备端部署指南

> 给**在 Jetson 板子上部署与调试机器人后端的人或 AI** 的说明。动手前请完整读一遍。
> 版本：2026-10-28，对应 API_CONTRACT v1.0。旧版（2026-10-20）描述的“Jetson 只采音放音、经 SSH 把音频交给服务器”的架构已经废弃。

---

## 1. 系统分工

```
┌──────────────────── 3080 服务器（局域网 192.168.0.239）────────────────────┐
│  Ollama：qwen3.5:9b 决策与看图（127.0.0.1:11435）                            │
│  GPT-SoVITS api_v2：训练音色合成（127.0.0.1:9881）                            │
└─────────────────────────────────────────────────────────────────────────┘
              ▲ 两个服务都只监听 3080 本机，Jetson 经 SSH 隧道访问
              │
┌──────────────────────────── Jetson（本机）────────────────────────────────┐
│  app.robot_server：任务、Agent 决策循环、动作执行、HTTP/SSE API（8090）        │
│  摄像头 → 观察；麦克风 → SenseVoice 常驻识别（CPU）；扬声器 ← 播放合成音频        │
│  串口 → STM32 底盘固件（CarAdapter，主机端里程计）                              │
└─────────────────────────────────────────────────────────────────────────┘
              ▲ 前端（Mac 桌面端）只连 Jetson 的 8090，不直接访问 3080
```

- 接口以根目录 `API_CONTRACT.md` 为唯一依据；Jetson 不新增接口，只部署和调试。
- GPT-SoVITS 能读取服务器上任意路径的参考音频，**绝不能**开放到局域网，只能经隧道访问。
- Jetson 不运行 GPT-SoVITS 或大模型；显存与内存留给摄像头、识别与后端本身。
- 3080 上的服务**不开机自启**：用机器人前在仓库根目录运行 `./model_server.sh start`，用完 `./model_server.sh stop` 释放显存。

### 你必须做的

- 每一步都留证据（命令与真实输出），未验证的如实写“未验证”。
- 需要 `sudo` 的操作（装系统包、开机自启 linger、加入 dialout 组）**先请示负责人**，不得自动执行。
- 底盘第一次上电运动前，先架空车轮或确认前方有空地，并准备好随时断电。

### 你绝对不能做的

- 把 3080 的 Ollama 或 GPT-SoVITS 改为监听局域网。
- 把 HTTP API 映射到公网。
- 在配置里写密码；SSH 一律用密钥。
- 修改 `domain/`、`runtime/`、`agent/` 等核心代码来“绕过”设备问题；发现问题写进汇报。

---

## 2. 本机环境现状（已实测，2026-10-20）

| 项目 | 实测值 |
|---|---|
| 型号 | `NVIDIA Jetson Orin NX Engineering Reference Developer Kit Super` |
| CPU / 内存 | 6 核 aarch64 / 7.3 GiB |
| 系统 | JetPack R39.2.1，Ubuntu 24.04，Python 3.12.3 |
| `uv` | **未安装** |
| `pip` | 系统 Python 无 pip，但 `python3 -m venv` 可用（venv 内自带 pip） |
| **PortAudio** | **未安装**（`libportaudio2` 缺，`sounddevice` 直接报 `PortAudio library not found`） |
| 音频命令行 | `arecord` / `aplay` 可用（ALSA 原生） |
| 家目录其他项目 | `Go2Agent`、`llama.cpp`、`frp`（与本项目无关，不要动） |

### 已识别的音频设备

| 设备 | 能力 | 说明 |
|---|---|---|
| `card 2` = `USB Audio Device`（C-Media `0d8c:0012`） | **仅播放** | 纯输出扬声器，没有采集端点。`PCM Playback Volume` 默认只有 14/37（约 -23 dB），**必须显式拉到 100%**，否则"播了没声音" |
| `card 3` = `Earpods`（Apple USB-C 耳机 `3654:5678`） | **采集 + 播放** | 采集 `S16_LE / 2ch / 48 kHz`，播放 `S16_LE 或 S24_3LE / 2ch / 44.1k 或 48k` |

> ⚠️ `card` 序号会随拔插变化，**配置里只能写名称**。

---

## 3. 环境准备

### 3.1 代码与依赖（不需要 sudo）

```bash
cd ~ && git clone https://github.com/SCMU-AILAB/teaching_robot_agent.git
cd ~/teaching_robot_agent
curl -LsSf https://astral.sh/uv/install.sh | sh        # 安装到 ~/.local/bin，不需要 sudo
~/.local/bin/uv sync --frozen --extra speech --extra car --extra depth
```

锁文件中的原生依赖（sherpa-onnx、onnxruntime、opencv、av）都有 linux aarch64 wheel，不需要编译；
若下载慢，可加 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`。

### 3.2 系统包与权限（**必须先请示**）

```bash
sudo apt update && sudo apt install -y libportaudio2   # sounddevice 依赖
sudo usermod -aG dialout "$USER"                        # 访问底盘串口，重新登录生效
sudo loginctl enable-linger "$USER"                     # 用户级服务开机自启
```

自检：`.venv/bin/python -c "import sounddevice; print(sounddevice.query_devices())"`。

### 3.3 识别模型

把 SenseVoice 模型目录（含 `model.int8.onnx` 与 `tokens.txt`）放到 `~/teaching_robot_agent/models/sensevoice-small/`。
根目录 `models/` 已被忽略，不会提交。

### 3.4 到 3080 的 SSH 隧道

Jetson 需要能免密钥登录 3080：

```bash
ssh-keygen -t ed25519            # 已有密钥可跳过
ssh-copy-id yaoyifeng@192.168.0.239
ssh -o BatchMode=yes yaoyifeng@192.168.0.239 echo ok
```

隧道由 `teaching-robot-tunnel.service` 常驻维护：Jetson 本机 `21435 → 3080 Ollama 11435`，`29881 → 3080 GPT-SoVITS 9881`。

---

## 4. 配置

程序不读取 `.env` 文件，配置全部来自环境变量。部署时由 systemd 从 `~/teaching-robot/robot.env` 读取：

```bash
mkdir -p ~/teaching-robot
cp deploy/jetson/robot.env.example ~/teaching-robot/robot.env
chmod 600 ~/teaching-robot/robot.env
```

逐项核对模板中的值。要点：

- **设备按名称配置**：`SPEECH_INPUT_DEVICE`、`SPEECH_OUTPUT_DEVICE` 用名称（子串、忽略大小写），不要填序号；
  找不到或同名多个时启动报错，不会退回系统默认。枚举：`.venv/bin/python -m providers.audio.worker devices`。
- **底盘**：`ROBOT_ADAPTER=car` 时 `CAR_SERIAL_PORT` 必填；`CAR_COUNTS_PER_METER`、`CAR_COUNTS_PER_RADIAN`
  未标定时底盘只连接、不开放运动技能。标定前可先用 `ROBOT_ADAPTER=simulated`。
- **摄像头**：`PERCEPTION_DEVICE` 是 OpenCV 序号（`/dev/videoN` 的 N）。RealSense D435i 占 `/dev/video0`–`5`，
  彩色画面是 `/dev/video4`（YUYV 640×480；`video2` 为灰度红外，其余 OpenCV 打不开）；接其他 USB 设备后序号可能变化，
  用 `cat /sys/class/video4linux/video*/name` 与 `curl -o f.jpg http://127.0.0.1:8090/api/v1/camera/snapshot` 核对。
- **端口**：`ROBOT_API_PORT=8090` 与前端默认一致，`ROBOT_API_HOST=0.0.0.0` 只用于受控局域网。

### 播放前必须设音量

C-Media 扬声器默认只有 14/37（约 -23 dB）：

```bash
for c in /sys/class/sound/card*; do
  [ "$(cat "$c/id" 2>/dev/null)" = "Device" ] && amixer -c "${c##*card}" sset PCM 100%
done
```

使用 `SPEECH_OUTPUT_DEVICE=pipewire` 时，PipeWire 的输出音量同时控制上面的硬件音量，还要执行
`wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0`（2026-10-30 实测：PipeWire 0.40 时声音很小）。

开机后才插入的 USB 声卡，WirePlumber 可能不为它启用输出配置，默认输出只剩 `Dummy Output`。
`wpctl status` 的 Sinks 中没有 `USB Audio Device Analog Stereo` 时，执行
`systemctl --user restart wireplumber`。

同时插着 Earpods 时，WirePlumber 可能把 Earpods 选为默认输出，`pipewire` 输出的声音会进耳机。用
`wpctl set-default <C-Media 输出编号>` 与 `wpctl set-default <Earpods 输入编号>` 指定（编号见 `wpctl status`）。
2026-10-31 实测：重启后 PipeWire 输出音量回到 0.41，默认设备也要重新核对，每次开机后检查。

---

## 5. 运行

### 5.1 手动前台运行（调试）

```bash
cd ~/teaching_robot_agent
ssh -N -L 21435:127.0.0.1:11435 -L 29881:127.0.0.1:9881 yaoyifeng@192.168.0.239 &   # 隧道
set -a; source ~/teaching-robot/robot.env; set +a
.venv/bin/python -m app.robot_server
```

启动日志应依次出现：设备已连接、`GPTSoVITSProvider._warmup 预热完成`、`机器人后端已启动`。
预热失败只记警告（例如隧道还没连上），首句播报会慢。

### 5.2 常驻服务

```bash
mkdir -p ~/.config/systemd/user
cp deploy/jetson/teaching-robot-tunnel.service deploy/jetson/teaching-robot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now teaching-robot-tunnel teaching-robot
journalctl --user -u teaching-robot -f
```

更新代码后：`git pull && ~/.local/bin/uv sync --frozen --extra speech --extra car --extra depth && systemctl --user restart teaching-robot`。

### 5.3 底盘标定（需要人在车旁）

```bash
.venv/bin/python -m app.car_calibrate /dev/ttyACM0 forward --permille 300 --seconds 2
.venv/bin/python -m app.car_calibrate /dev/ttyACM0 turn --permille 300 --seconds 2
```

按提示输入实测距离或角度，把打印的 `CAR_COUNTS_PER_*` 写入 `robot.env` 后重启服务。标定需要独占串口，先停后端。

固件停车时电机滑行、不制动：落地 10 cm/s 停车后还会滑行约 3.3 cm。`CarAdapter` 按遥测测得的速度与
`CAR_COAST_COUNTS_PER_S2`（默认 2700）提前停车；换车或换地面后若到位误差明显，先重测该值。

### 5.4 深度避障与自由探索（需要人在车旁）

RealSense 深度节点经 V4L2 直接读取（`linuxpy`，不需要 librealsense 或 sudo）。设计见
`docs/superpowers/specs/2026-10-31-exploration-obstacle-design.md`。

1. 车放在空地上，前方 1.5 米内没有东西，标定摄像头安装（不需要停后端，车不动）：

   ```bash
   .venv/bin/python -m app.depth_calibrate mount
   ```

   把打印的 `DEPTH_CAMERA_HEIGHT_M`、`DEPTH_CAMERA_PITCH_DEG` 写入 `robot.env`，并设 `DEPTH_CAMERA=realsense`。
   同时打印车前盲区：该距离以内贴地的东西深度相机看不见。四周有椅子挡住地面时会报
   “No dominant floor plane found”，换到空地再测。
2. 摆一个纸箱，核对空隙读数：`.venv/bin/python -m app.depth_calibrate watch --seconds 20`。
3. 重启后端。之后所有前进都经过避障守护：前方空隙小于 `OBSTACLE_STOP_M`（默认 0.4 米）加滑行量、
   深度超过 0.5 秒未更新、或贴近到读不出距离时停车，动作失败原因为“前方有障碍物”或“看不清前方”。
   摄像头看不到后方，单次后退限 0.3 米。
4. 对机器人说“去探索一下吧”：本条回答播完后在后台探索，离起点 `EXPLORE_RADIUS_M` 内、最长
   `EXPLORE_MAX_S` 秒，发现新物体停下讲一句；说话或停止任务会先停下探索。

D435i 的加速度计在 `/dev/hidraw3`，只有 root 可读；要用它测俯仰需加 udev 规则（需请示）。

---

## 6. 验收自检清单

按顺序做，每条都要有证据。

| # | 检查项 | 命令 / 判据 |
|---|---|---|
| 1 | 隧道 | `curl -s http://127.0.0.1:21435/api/tags` 列出 `qwen3.5:9b`；`curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:29881/tts` 有响应（非连接失败） |
| 2 | 后端状态 | `curl -s http://127.0.0.1:8090/api/v1/system`：`robot.connected`、`camera.available`、`audio.available` 为 true，`services` 全为 `ready` |
| 3 | 局域网可达 | 在 Mac 上 `curl http://<Jetson 地址>:8090/api/v1/system` 成功 |
| 4 | 摄像头 | `curl -o f.jpg http://127.0.0.1:8090/api/v1/camera/snapshot` 得到正确画面 |
| 5 | 文字问答 | 前端建任务后问“看看桌上有什么”：先出现观察事件，再出现回答与播报，人工确认听到 |
| 6 | 语音问答 | 前端录音提问，转写正确并得到播报回答 |
| 7 | 底盘 | 标定后让机器人“向左转一点点”“往前走二十厘米”，动作 succeeded 且实际位移与指令相符 |
| 8 | 停止 | 运动中点停止，任务 `cancelled`，车轮在 1 秒内停下 |
| 9 | 拔线看门狗 | 运动中拔掉串口线，底盘 300 ms 内自行停车 |
| 10 | 断网恢复 | 断开再恢复 Jetson 网络，隧道自动重连，下一句问答正常 |

---

## 7. 编码规范（必须遵守）

来源：本目录内 `CODING_SPEC.pdf`（v1.0 原件），主仓库另有 Markdown 版 `docs/CODING_SPEC.md`。两者冲突时以 PDF 原件为准，并按主仓库实际配置（`pyproject.toml` 的 ruff / basedpyright 规则）执行。

- Python ≥ 3.12；类型标注完整，用 `X | None`，禁用 `Optional`。
- 标识符英文，注释与 docstring 中文；Google 风格，摘要以句号结尾。
- 每个源文件第一行写相对路径注释，如 `# providers/audio/worker.py`。
- 复杂流程用 `# ========== StepN: 说明 ==========` 分段。
- 日志格式 `[ClassName.method_name] 描述`，用延迟格式化（`logger.info("... %s", x)`），不要 f-string 拼日志。
- **模块导入时零副作用**：不得在 import 时打开设备、启动线程、拉起子进程、连 SSH。
- 错误要分类清楚：设备不可用、网络失败、超时、取消，不能混成一个 `Exception`。
- 提交信息格式：`<type>(<scope>): <subject>`，type 取 feat/fix/refactor/docs/test/chore/perf。

交付前四项检查（全过才算完成）：

```bash
ruff check .
ruff format --check .
basedpyright          # 要求 0 error 0 warning
python -m unittest discover -s tests
```

> 板子上按第 3 节装好 `uv` 后，用 `uv run` 执行这四项；也可以放到主仓库跑。

---

---

## 8. 汇报格式

每次交付按这个结构写，缺项视为未完成：

```
1. 改了什么：文件 + 行为变化
2. 怎么验证的：命令 + 真实输出（截图/日志）
3. 已验证：……
4. 未验证：……（如实列出，不要含糊）
5. 需要人工决定：……（sudo、协议、硬件接入等）
```

---

---

## 9. 当前已知问题

| 问题 | 现状 |
|---|---|
| 缺 PortAudio | 需人工安装 `libportaudio2`（第 3.2 节） |
| 扬声器默认音量过低 | 14/37（-23 dB）且 PipeWire 0.40；按第 4 节拉到 100%，重启后 PipeWire 音量会复原，需重设 |
| C-Media 声卡无采集 | 纯输出设备，麦克风必须用 Earpods 那类带采集的设备 |
| 内存被其他项目占用 | `Go2Agent`、`llama.cpp`、`frp` 已占约 6 GiB，联调时注意别抢资源 |
| 3080 内存紧张 | GPT-SoVITS 闲置半小时后首句可能需要数十秒；后端只在启动时预热一次 |
| 底盘标定精度有限 | 2026-10-30 以 10 cm 尺量、30° 目测标定；落地前进 ±20 cm 到位误差 0.4 mm 以内（按里程计）；拔线停车未测；里程计看不到打滑 |
| 深度相机视野 | 摄像头朝上倾斜，车前近处地面不在视野内，贴地矮小物体看不见；深度 0.3 米内无读数 |
| 公网 SSH 入口不稳 | 经公网入口的 SSH 会话每隔几分钟断开一次；联调改用局域网 SSH，正式部署用 Jetson 直连 3080 的隧道 |
| 本指南的部署步骤 | 2026-10-28 编写时 Jetson 不在线，**整套步骤尚未在板子上实际执行过** |
