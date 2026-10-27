# Jetson 设备端开发指南

> 本文件是给**在 Jetson 板子上工作的 AI / 开发者**的任务说明。请先完整读一遍再动手。
> 版本：2026-10-20，对应主仓库提交 `a73da60`。

---

## 1. 你的角色

你负责的是**设备端**（Jetson Orin NX + USB 声卡），不是服务端，也不是算法端。

系统分工：

```
                     ┌──────────────── 服务器（不在本机）────────────────┐
学生说话             │  Whisper/SenseVoice ASR │ 教学 Agent 推理 │ Piper TTS │
   │                 └───────────────────────────────────────────────┘
   ▼                         ▲ 音频上行              │ 音频下行
┌────────────────────────── Jetson（你负责这里）──────────────────────────┐
│  采音（麦克风） → 上传 →  接收文本 → 接收音频 → 播放（扬声器）           │
│  接收控制决策 → 转换为运动指令 → 串口下发 → ESP32 → 小车电机            │
└──────────────────────────────────────────────────────────────────────┘
```

**一句话**：Jetson 是"耳朵、嘴和手"，大脑在服务器。

### 你必须做的

1. 采集麦克风音频，按约定格式交给服务器识别。
2. 接收服务器返回的音频并播放，能中途停止并确认停止。
3. 接收上层的运动决策，翻译成 ESP32 能执行的指令并下发。
4. 保证设备资源（声卡句柄、子进程、串口）在取消、异常、退出时全部释放。

### 你绝对不能做的

| 禁止项 | 原因 |
|---|---|
| 在板子上加载 ASR / TTS 模型（SenseVoice、Whisper、Piper 等） | 推理和合成只在服务器执行，板端不做推理 |
| 自己决定或猜测服务器、ESP32 的通信协议 | 协议未定稿，必须先上报，等负责人确认 |
| 自动执行 `sudo`、自动 `apt install`、改系统配置 | 需要人工批准；当前 `libportaudio2` 就属于这种情况 |
| 修改 `runtime/`、`robot/`、`skills/`、`domain/`、`storage/` 里的共享模型 | 属于核心层，改动会影响服务器侧与前端 |
| 修改 `docs/API_CONTRACT.md`、`docs/MODULE_CONTRACT.md` | 合同变更必须先提审 |
| 用硬编码序号选声卡（如 `plughw:2,0`） | 序号随拔插漂移，必须按名称 |
| 把模拟结果当成真实验证结果汇报 | 验收要求区分"已验证 / 未验证" |
| 为了跑通而加 `noqa`、`ignore`、`type: ignore` | 违反编码规范 |

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

## 3. 目录结构

```
~/teachingrobotagent/
├── README.md                    ← 本文件
├── CODING_SPEC.pdf              ← 代码规范原件（v1.0，与下文第 9 节对应）
├── requirements.txt             ← 设备端唯一第三方依赖（sounddevice）
├── .env.example                 ← 环境变量模板
├── domain/                      # 共享数据模型（只读，不要改）
│   ├── services.py              # AudioReference / TranscriptResult / PlaybackState
│   ├── models.py / robot.py / education.py
│   └── validation.py            # 入参校验工具
├── storage/
│   ├── evidence.py              # 证据存储：按不透明 ID 存音频，不暴露文件路径
│   └── memory.py
├── providers/
│   ├── audio/
│   │   ├── worker.py            # 设备工作子进程：唯一真正打开声卡的地方
│   │   ├── wav.py               # PCM/WAV 编解码
│   │   ├── recorder.py          # PortAudioRecorder：录音生命周期
│   │   └── real_player.py       # PortAudioPlayer：播放与停止确认
│   ├── asr/remote.py            # RemoteASRProvider：上传音频给服务器识别
│   └── tts/remote.py            # RemoteTTSProvider：请求服务器合成并落地为证据
├── speech/
│   ├── config.py                # 环境变量装配
│   ├── remote.py                # SSH 传输层（标准输入输出，不新开端口）
│   ├── _process.py              # 子进程统一管理
│   ├── _lifecycle.py            # 取消与清理辅助
│   ├── resources.py             # AudioDeviceLease：录音与播放互斥
│   ├── interfaces.py            # Protocol 定义
│   ├── input.py                 # SpeechInputService：录音 → 识别 → 提交
│   ├── output.py                # SpeechOutputService：合成 → 播放 → 终止态
│   └── real_demo.py             # 设备端可运行入口（固定回答，非真实 Agent）
├── app/team_gateway.py          # 输入队列门面（只读）
├── runtime/  robot/  skills/    # 核心层，real_demo 依赖它们；只读，不要改
└── perception/                  # 感知接口与模拟实现（只读）
```

> `runtime/`、`robot/`、`skills/`、`perception/` 只是因为 `speech/real_demo.py` 依赖才带过来，
> **不是给你改的**。你的工作范围是 `providers/audio/`、`providers/asr/remote.py`、
> `providers/tts/remote.py`、`speech/`（`config.py`、`remote.py`、`input.py`、`output.py`、
> `resources.py`），以及将来的 ESP32 控制模块。

---

## 4. 环境准备

### 4.1 建立虚拟环境（不需要 sudo）

```bash
cd ~/teachingrobotagent
python3 -m venv .venv
./.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

### 4.2 安装 PortAudio（**必须先请示，不得自动执行**）

当前板子缺 `libportaudio2`，`sounddevice` 无法加载。安装命令：

```bash
sudo apt update && sudo apt install -y libportaudio2
```

- 这条命令**需要人工批准**，执行前先在汇报里说明"我准备执行 apt 安装，请确认"。
- 装完自检：`.venv/bin/python -c "import sounddevice; print(sounddevice.query_devices())"`。

> 在 PortAudio 装好之前，只能用 ALSA 命令行（`arecord` / `aplay`）验证硬件，
> **不能**说"语音链路已验证"。

### 4.3 服务器连通性

设备端通过 `ssh` 标准输入输出与服务器通信，**不需要新开端口、不使用 HTTP**：

```bash
ssh -T -p 22 <用户>@<服务器地址> "echo ok"
```

---

## 5. 配置

程序**不会自动读取 `.env`**，环境变量需要在启动前导出。

| 变量 | 含义 | 示例 |
|---|---|---|
| `SPEECH_SSH_HOST` | 服务器地址 | `10.0.0.5` |
| `SPEECH_SSH_USER` | 服务器用户名 | `speech` |
| `SPEECH_SSH_PORT` | SSH 端口 | `22` |
| `SPEECH_SERVER_DIRECTORY` | 服务器语音安装目录（绝对路径） | `/opt/speech` |
| `SPEECH_INPUT_DEVICE` | **录音设备名称** | `Earpods` |
| `SPEECH_OUTPUT_DEVICE` | **播放设备名称** | `USB Audio Device` |
| `SPEECH_SAMPLE_RATE` | 采样率，默认 16000 | `16000` |
| `SPEECH_MAX_DURATION_S` | 单次录音上限，默认 15 | `15` |
| `SPEECH_TIMEOUT_S` | 单次往返超时，默认 60 | `60` |
| `SPEECH_ASR_LANGUAGE` | 识别语言 | `zh` |
| `SPEECH_TTS_VOICE` | 音色 | `huayan` |

### 设备必须按名称配置

代码用 `providers/audio/worker.py` 的 `resolve_device()` 解析：空名称走系统默认；
否则按名称匹配（完整名 > 前缀 > 子串，忽略大小写），并校验该设备是否具备对应方向
（录音看 `max_input_channels`，播放看 `max_output_channels`）。

- 找不到设备 → 抛 `ValueError`，**不会静默退回系统默认**。
- 多个设备同名 → 抛 `ValueError` 并列出候选，要求配更精确的名称。
- **不支持数字序号**，因为序号随拔插漂移。

查设备列表：

```bash
./.venv/bin/python -m providers.audio.worker devices
```

### 播放前必须设音量

```bash
amixer -c "$(basename "$(readlink -f /sys/class/sound/card*/device 2>/dev/null | head -1)")" sset PCM 100%
```

更实用的写法是按名称定位：

```bash
for c in /sys/class/sound/card*; do
  [ "$(cat "$c/id" 2>/dev/null)" = "Device" ] && amixer -c "${c##*card}" sset PCM 100%
done
```

---

## 6. 运行入口

```bash
cd ~/teachingrobotagent
set -a; source .env.speech; set +a       # 或手动 export
./.venv/bin/python -m speech.real_demo
```

控制台命令：

| 命令 | 作用 |
|---|---|
| `/record` | 开始录音 |
| `/finish` | 结束录音并送识别 |
| `/stop` | 取消当前这一轮（录音/识别/合成/播放） |
| `/quit` | 退出并释放设备 |

一次正常交互的输出顺序应为：`正在录音…` → `识别：<文本>` → `正在合成…` → `播放结果：completed`。

> 这个入口用的是**固定测试回答**，不是真实 Agent 回答。它的作用是验证
> "采音 → 服务器识别 → 服务器合成 → 本地播放"这条链路本身。

---

## 7. 验收自检清单

按顺序做，每条都要有证据（命令输出或日志），不能只写"通过"。

| # | 检查项 | 命令 / 判据 |
|---|---|---|
| 1 | 声卡枚举 | `cat /proc/asound/cards` 能看到 C-Media 与 Earpods |
| 2 | 设备名称解析 | `python -m providers.audio.worker devices` 输出包含两者 |
| 3 | 录音可用 | 录 3 秒后统计采样点，峰值 > 300 视为有输入 |
| 4 | 播放可用 | 播放 1 kHz 测试音，**人工确认听到** |
| 5 | 音量已设置 | `amixer -c <n> sget PCM` 显示 100%（0 dB） |
| 6 | SSH 连通 | `ssh -T -p 22 user@host "echo ok"` 返回 ok |
| 7 | 单轮往返 | `/record` → `/finish`，记录"结束录音"到"文本返回"的耗时 |
| 8 | 播放停止 | 播放中 `/stop`，终态必须是 `stopped`（不是 `completed`） |
| 9 | 资源释放 | 取消后 `ps aux | grep worker` 无残留；`ls /proc/asound/card*/pcm*p/sub0/status` 无 `RUNNING` |
| 10 | 反复插拔 | 拔插声卡后重新运行，设备仍能按名称找到 |

---

## 8. 待实现：ESP32 运动指令下发

**当前仓库里没有这部分代码，也没有定稿协议。** 你需要做的是先上报方案，不要自己定。

已知约束：

- Jetson 与 ESP32 之间大概率是串口/UART（也可能是 USB CDC），**传输方式待确认**。
- 指令需要覆盖：前进、后退、左转、右转、停止、速度设置；是否要闭环反馈待确认。
- 必须支持**急停**：任何异常或超时都要能回到停止状态。
- 下发必须与其他任务解耦：语音播报不能阻塞运动停止。

上报时按这个格式写：

1. 传输方式（UART 设备名、波特率）与协议草案（帧格式、校验、应答）。
2. 失败与超时策略（重发次数、超时后是否强制停车）。
3. 与语音链路的资源冲突分析（是否共用同一线程/事件循环）。
4. 测试方法（无硬件时如何用假串口验证）。

在负责人确认前，只能写**不落地的设计文档**，不能写死协议常量。

---

## 9. 编码规范（必须遵守）

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

> 板子上没有 `uv`，可以直接用 `./.venv/bin/ruff`、`./.venv/bin/basedpyright`，
> 或把这四项放到主仓库跑。

---

## 10. 汇报格式

每次交付按这个结构写，缺项视为未完成：

```
1. 改了什么：文件 + 行为变化
2. 怎么验证的：命令 + 真实输出（截图/日志）
3. 已验证：……
4. 未验证：……（如实列出，不要含糊）
5. 需要人工决定：……（sudo、协议、硬件接入等）
```

---

## 11. 当前已知问题（不要重复踩）

| 问题 | 现状 |
|---|---|
| 缺 PortAudio | `sounddevice` 无法加载，`providers.audio.worker` 跑不起来，需人工装 `libportaudio2` |
| 扬声器默认音量过低 | 14/37（-23 dB），必须 `amixer` 拉到 100% |
| C-Media 声卡无采集 | 纯输出设备，麦克风必须用 Earpods 那类带采集的设备 |
| 板子无 `uv` | 用 `python3 -m venv` + pip（清华源） |
| 内存被其他项目占用 | `Go2Agent`、`llama.cpp`、`frp` 已占约 6 GiB，联调时注意别抢资源 |
