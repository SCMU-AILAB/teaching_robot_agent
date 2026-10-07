# 真实语音独立链路

## 选型与范围

本轮沿用已有 AudioRecorder、ASRProvider、TTSProvider、AudioPlayer Protocol，实现独立的真实语音演示，不增加 HTTP/SSE，不修改 Runtime、机器人驱动、教学 Agent 或共享模型。

| 环节 | 首版实现 | 完成依据 |
| --- | --- | --- |
| 麦克风 | sounddevice / PortAudio，单声道十六位 PCM | 工作进程打开并启动设备后确认就绪；结束后实际 WAV 帧登记到 EvidenceStore |
| ASR | 本地 faster-whisper，多语言 base 模型起步，CPU int8 | VAD 过滤静音，消费全部识别段后返回最终 TranscriptResult |
| TTS | macOS say 中文音色；Linux eSpeak NG 中文音色 | 引擎实际生成 WAV，校验成功后返回 AudioReference；此时没有播放 |
| 扬声器 | sounddevice / PortAudio 阻塞输出流 | write 完成后 stop 排空设备缓冲、close 释放，才确认 completed |

ASR 可通过环境变量改用其他已下载的 Whisper 模型或 CUDA。每次识别使用独立进程，优点是超时和取消可以终止实际推理；代价是每轮重新加载模型。首版优先验证生命周期，不宣称达到常驻推理服务的延迟水平。

系统 TTS 使用真实语音合成，不是测试音调。macOS 与 Linux 引擎的音色和自然度不同；本轮实际验证的是 macOS，Linux 路径未做硬件验收。

参考：[sounddevice](https://python-sounddevice.readthedocs.io/)、[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[多语言 base 模型](https://huggingface.co/Systran/faster-whisper-base)、[eSpeak NG](https://github.com/espeak-ng/espeak-ng)。依赖由 uv.lock 锁定；faster-whisper 1.2.1 与 PyAV 19 的 metadata_errors 参数不兼容，因此 speech 可选依赖约束 av<19。

## 安装和设备配置

```bash
uv sync --locked --extra speech
uv run --extra speech python -m providers.audio.worker devices
```

macOS 已包含 say；首次访问麦克风时需允许实际运行终端或应用的麦克风权限。Linux 需系统管理员安装 PortAudio 与 espeak-ng，并确认当前用户可访问相应音频设备。不要把检测到 `/dev/snd` 文件等同于具备设备访问权限。

模型需提前下载一次。下面命令返回本地目录；之后把该目录设置为 SPEECH_ASR_MODEL_PATH。识别适配器只使用本地文件，不在学生录音时自动下载模型。

```bash
uv run --extra speech python -c 'from huggingface_hub import snapshot_download; print(snapshot_download("Systran/faster-whisper-base", allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]))'
```

环境变量在 `.env.example` 中有完整示例。应用不会自动加载 `.env`，在运行终端导出所需配置：

```bash
export SPEECH_ASR_MODEL_PATH=/path/to/downloaded/model
export SPEECH_SAMPLE_RATE=16000
export SPEECH_MAX_DURATION_S=15
export SPEECH_TIMEOUT_S=60
export SPEECH_ASR_LANGUAGE=zh
export SPEECH_ASR_DEVICE=cpu
export SPEECH_ASR_COMPUTE_TYPE=int8
export SPEECH_TTS_ENGINE=say
export SPEECH_TTS_VOICE=Tingting
uv run --extra speech python -m speech.real_demo
```

SPEECH_INPUT_DEVICE、SPEECH_OUTPUT_DEVICE 留空表示系统默认设备，也可填写枚举编号或名称。Linux 将 TTS 配置改为 `SPEECH_TTS_ENGINE=espeak-ng`、`SPEECH_TTS_VOICE=cmn`。设备采样率必须被实际驱动支持，失败不会偷偷切换设备或返回模拟音频。

本方案使用本机离线引擎，无 ASR/TTS 服务 URL、无云端凭据。服务器 SSH 地址、账号和密码没有写入仓库。通过 SSH 运行时使用的是服务器音频设备，不会自动使用客户端 Mac 的麦克风。

## 演示与验收操作

1. 输入 `/record`，等待“正在录音”后说一句话。
2. 输入 `/finish` 或等待最长时长自动结束，控制台显示最终转写。
3. 非空最终文本通过 TeamGateway.submit_transcript() 生成 UserInput；示例唯一消费输入队列，随后合成并实际播报固定回答。
4. 录音、识别、合成或播放期间输入 `/stop` 取消本轮；清理完成后才能进入下一轮。
5. 输入 `/quit` 或 Ctrl+C 退出，当前作业及设备必须关闭。
6. 连续操作三轮，确认每轮只出现一次有效输入，之后仍可录音。

这是独立进程中的演示输入消费者，不得放到已有教学消费者旁竞争读取同一队列。固定回答是“我已经听到你的回答，这是语音链路测试。”，不代表 Agent 自动生成了回答。

空语音正常返回空文本，示例不提交核心、不合成、不播放。零帧、损坏 WAV、无设备、采集溢出、引擎失败、超时则明确失败，不能伪装成空语音。中文识别可能输出繁体字；本轮不额外改写识别结果。

## 取消与资源所有权

- 录音器与播放器必须共用同一个 AudioDeviceLease。识别和合成本身不占用音频设备，播放与录音不能并行占用。
- 录音启动一次后只有一个结果作业，主动结束发送结束信号，自动结束由工作进程执行；重复 finish、自动结束竞态不会二次登记音频。
- 识别、合成在独立工作进程运行。取消或超时先结束进程，再等待回收；创建进程时发生取消也会取得句柄后回收，不遗留孤儿进程。
- 录音取消丢弃帧，不保存迟到音频。SpeechInputService 继续负责取消标记与提交前检查，TeamGateway 继续负责任务有效性及去重。
- 播放停止必须收到工作进程执行 abort/close 后的 STOPPED 确认。停止恰逢自然完成时保留 completed；缺少确认报告 failed，不能假称 stopped。
- 播放工作进程挂起时会被终止并回收，但强制终止不冒充正常驱动停止确认。播放器失败后禁止复用该实例，调用方需检查设备并重新装配。
- close/cancel/stop 使用已有 finish_cleanup；重复外部取消不能打断回收。TTS 临时目录在成功、失败、超时和取消后删除。
- EvidenceStore 继续保留已登记的音频证据，关闭设备不等于删除证据。容量管理仍沿用既有内存存储，未自行增加过期策略。

completed 表示软件驱动确认音频流已排空并关闭；音量为零、扬声器未插入或外部硬件故障仍需要现场听辨，不能仅靠进程退出证明学生听到了声音。

## 文件交接

| 文件 | 职责 |
| --- | --- |
| providers/audio/recorder.py | 真实录音生命周期、唯一结果、设备占用与证据登记 |
| providers/audio/real_player.py | 真实播放状态、停止确认与关闭 |
| providers/audio/worker.py | 在隔离进程调用 PortAudio 采集和播放 |
| providers/audio/wav.py | 有界 PCM WAV 校验、证据读取与登记 |
| providers/asr/whisper.py、worker.py | 真实离线 ASR 适配、最终结果解析与推理进程 |
| providers/tts/system.py | 系统 TTS、临时音频清理，不自行播放 |
| speech/_process.py | 可取消子进程启动、终止与回收 |
| speech/config.py、real_demo.py | 环境配置与独立终端演示 |
| tests/test_real_speech.py、speech_worker_fixture.py | 真实适配器编排的进程级测试，设备和引擎使用受控测试替身 |

## 本轮实际验证与限制

2026-10-07，在当前 Mac 上完成：

- 枚举内置麦克风和扬声器；三次连续真实录音均生成 0.3 秒 PCM WAV，每次完成后租约空闲。
- say 中文音色实际生成 16 kHz WAV；离线 Whisper 对“你好，这是语音链路测试。”返回“你好,這是語音鏈路測試”。这是合成人声识别，不代表真人准确率评估。
- 一秒纯静音经真实 Whisper 返回空文本。
- 真实播放器正常排空返回 completed；中途停止返回 stopped，清理后租约空闲。
- 独立控制台运行真实录音到最终转写，再合成固定回答并播放返回 completed；随后开始新录音并停止、退出，无错误。录入内容没有预先标注，不能据此评定识别准确率。

服务器只做了环境检查：有 GPU 和声卡设备节点，但给定 SSH 账号运行 arecord/aplay 均报告无法找到声卡；没有修改用户组、系统权限、已有服务或远端部署。服务器硬件验收仍需现场确认接入的麦克风/扬声器及账号权限。

自动化测试使用可控子进程，检查最终状态、次数、PID 已退出、后台任务和设备租约，而非仅检查“不抛异常”。现场仍需学生说话、听辨实际输出并操作中途停止。未连接 frontend、HTTP/SSE 或核心的教学回答自动播报，也未接管全任务取消职责。

本轮新增 13 项进程级测试；全仓库 165 项测试通过。`uv run ruff check .`、`uv run ruff format --check .` 均通过，`uv run basedpyright` 为 0 错误、0 警告。演示退出后检查未发现遗留音频、ASR 或测试工作进程。前端未修改，本轮没有重复运行 Flutter 检查。
