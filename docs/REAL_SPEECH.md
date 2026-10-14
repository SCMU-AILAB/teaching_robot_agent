# 本地设备与服务器语音推理

## 部署职责

服务器没有声卡，也不需要声卡。本地 Mac 或机器人负责录音和播放；服务器负责 ASR 和 TTS。当前默认入口已按这一分工装配，不再默认在 Mac 执行识别和合成。

```text
本地麦克风 → 本地 WAV 证据 → SSH 上传 → 服务器 Whisper ASR
  → 最终文本返回本地 → TeamGateway.submit_transcript() → UserInput

固定回答文本 → SSH 发送 → 服务器 Piper TTS → WAV 返回
  → 本地 EvidenceStore → 本地 PortAudio 播放 → 实际播放终态
```

| 位置 | 实现 | 约束 |
| --- | --- | --- |
| 本地录音 | sounddevice / PortAudio | 设备启动后才返回；主动结束、自动结束共享一份结果 |
| 服务器 ASR | faster-whisper、多语言 base、本轮 CPU int8 | 只加载服务器模型；VAD 过滤静音；失败不伪装为空文本 |
| 服务器 TTS | Piper 1.8.0，中文 huayan-medium ONNX 模型 | UTF-8 文本生成 PCM WAV 文件，不调用音频输出设备 |
| 本地播放 | sounddevice / PortAudio | 驱动排空后才 completed，停止须确认 abort/close |
| 内部传输 | OpenSSH，无 PTY，标准输入输出有界帧 | 不新增 HTTP 端点或监听端口，不改变页面合同 |

录音器和播放器共用本地 AudioDeviceLease。服务器不取得设备租约、不导入音频设备工作进程、不需要 PortAudio 或 sounddevice。Whisper/Piper 类作为服务器执行组件，原 SystemTTS 保留供独立测试，默认演示不使用本地推理回退。

## 当前机器直接运行

本机已创建 Git 忽略的 `.env.speech`，只包含服务器连接配置，没有密码。已有 SSH 认证可用；客户端使用 BatchMode 和严格主机密钥校验，不弹密码提示、不把密码放到命令行。

```bash
uv sync --locked --extra speech-client
set -a
source .env.speech
set +a
uv run --extra speech-client python -m speech.real_demo
```

本地只需音频设备依赖，不需要下载模型或安装 TTS。应用不会自动加载环境文件；新机器按 `.env.example` 配置以下值：

- SPEECH_SSH_HOST、SPEECH_SSH_USER、SPEECH_SSH_PORT：服务器 SSH 连接信息。
- SPEECH_SERVER_DIRECTORY：服务器语音安装目录，必须是绝对路径。
- SPEECH_INPUT_DEVICE、SPEECH_OUTPUT_DEVICE：本地设备编号/名称，空值为系统默认。
- SPEECH_SAMPLE_RATE：默认 16000；SPEECH_MAX_DURATION_S：默认 15，最长 60 秒。
- SPEECH_TIMEOUT_S：默认 60，包含连接、传输及推理，最大 300 秒。
- SPEECH_ASR_LANGUAGE：默认 zh；SPEECH_TTS_VOICE：默认 huayan。未部署的音色明确拒绝。

枚举本地设备：`uv run --extra speech-client python -m providers.audio.worker devices`。本地仍需麦克风授权和扬声器；这些设置与服务器声卡无关。

## 操作流程

1. 输入 `/record`，等待“正在录音”后说话。
2. `/finish` 主动结束，或等待最大时长。音频上传服务器识别，控制台显示最终转写。
3. 最终非空文本才提交核心；固定回答由服务器合成，音频下载后在本地播放。
4. 任何阶段用 `/stop` 取消本轮；`/quit` 或 Ctrl+C 退出并清理。
5. 连续三次验证设备可重复打开；现场确认播放停止后不再发声。

固定回答不是 Agent 生成的回答。独立演示拥有自己的输入消费者，不得与现有教学消费者竞争读取同一队列。

## 服务器安装记录

2026-10-07 已在指定服务器当前账号的 `~/opt/teaching-robot-speech` 完成隔离部署：

- `.venv`：Python 3.12，faster-whisper 1.2.1、av 18.1.0、piper-tts 1.8.0 及依赖。
- `models/whisper-base`：从本机已验证模型缓存传输的多语言 base 模型。
- `models/piper/`：中文 huayan-medium 模型、ONNX 配置及原始 MODEL_CARD。
- `domain/`、`storage/`、语音 provider 和 `speech/remote_worker.py` 等必要 Python 文件。
- `constraints.txt`：从当前 uv.lock 导出的版本约束，安装后已再次校验。

没有更改系统声卡权限、用户组、已有服务或全局 Python；没有启动常驻服务。每次 SSH 请求启动一个有期限的作业，完成即退出。

服务器默认使用安装目录下的 `models/whisper-base` 与 `models/piper/zh_CN-huayan-medium.onnx`。如需调整，通过远端进程环境设置 SPEECH_ASR_MODEL_PATH、SPEECH_ASR_DEVICE、SPEECH_ASR_COMPUTE_TYPE、SPEECH_TTS_MODEL_PATH、SPEECH_TTS_VOICE；这些变量属于服务器，客户端不检查远端模型是否在本地存在。本轮为服务器 CPU 推理，不代表 CUDA 环境已验收。

重建环境时从项目生成约束：

```bash
uv export --extra speech-server --no-dev --no-hashes --no-emit-project --format requirements-txt --output-file /tmp/speech-constraints.txt
```

将约束、必要源码和模型传到远端安装目录，再在服务器执行：

```bash
uv venv --python python3.12 .venv
uv pip install --python .venv/bin/python -c constraints.txt faster-whisper av piper-tts
```

不在服务器安装音频设备依赖。服务器源码需要包含完整 domain/storage 包，因为其包入口会导入共享模型和存储实现。ASR 模型来源为 Systran/faster-whisper-base；TTS 来源为 rhasspy/piper-voices 的 `zh/zh_CN/huayan/medium`，ONNX 和同名 `.onnx.json` 必须同时部署。原始 MODEL_CARD 随模型保留，其中数据集许可证字段标为 Unknown；本轮模型用于内部联调，未将权重提交仓库。

## 内部传输与取消

这是两个语音适配器之间的内部 SSH 作业协议，不是公共 HTTP API。请求由 JSON 头及定长正文组成，只允许 asr/tts、大小、期限和语言/音色；不接受客户端指定任意命令、文件路径或模型目录。ASR 返回最终文本 JSON，TTS 返回 WAV；客户端实际校验字段和媒体后才进入现有服务。

客户端发完正文仍保持标准输入打开。取消或超时关闭输入，服务器监控 EOF，取消模型工作进程并等待回收，然后发送取消确认。远端收到 SIGHUP/SIGTERM 时也进入同一取消路径。迟到文本和音频不会在客户端重新提交或播放。

服务器另外有独立执行期限，最大 300 秒；请求头/正文读取限时 10 秒，响应写入限时 5 秒。断网时不能承诺立即检测到 EOF：客户端如果在清理期限内没有收到确认，会明确报“远端清理未确认”，不假装已终止；服务器自身期限仍限制已经启动的作业。不会自动重试识别或回退本地推理。

本地录音/播放依旧用可回收设备进程；驱动完成、停止和失败沿用 PlaybackState。TTS 完成仅代表音频生成。停止播放不等待服务器再次生成文本。既有证据保留在本地内存存储；远端单次进程退出后释放请求音频，TTS 临时文件在退出路径删除。

## 验证与限制

真实服务器联调已验证：

- 预先准备的中文语音经远端 ASR 返回最终文本；“识别”有一次识别为“时别”，不宣称识别准确率已达标。
- 一秒静音经远端 ASR 返回空文本。
- 服务器生成 22050 Hz 中文 WAV，在 Mac 播放返回 completed；中途停止返回 stopped，设备租约释放。
- 取消 ASR 和 TTS 请求都收到远端结束确认；随后进程检查没有遗留 remote_worker、ASR 或 Piper 合成进程。

TTS 已采用 Piper 中文神经音色，服务器合成的测试句再经 ASR 能返回相近文本，但仍有同音字错误；这不代替现场听辨或识别准确率评估。学生现场说话、最终机器人设备和教学回答自动连接仍需验收；没有新增 frontend、HTTP/SSE 或全任务取消实现。

本轮试验发现服务器系统 eSpeak 的中文发音效果不满足联调要求，最终默认装配已改为 Piper。用户目录下保留了诊断时提取的 eSpeak 二进制，但默认远端入口不再调用它。更换模型或音色后必须重新验证中文发音，不能只检查生成了 WAV。

新增进程级回归验证双端传输、取消父子进程回收、断线丢弃迟到结果、服务器独立期限、非法请求拒绝，以及客户端不需要本地模型。原有设备生命周期与核心输入去重测试继续保留。

最终验证：新增 9 项测试，全量 174 项 unittest 通过；Ruff 检查、129 个文件格式检查通过，basedpyright 为 0 错误、0 警告。默认演示已用本地麦克风完成远端空语音识别，并验证下一轮停止、退出清理；退出后服务器无遗留语音进程。未修改 Runtime、机器人层、Teaching Agent、共享领域模型、API_CONTRACT 或 MODULE_CONTRACT。
