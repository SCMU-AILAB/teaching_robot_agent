# 内部模拟语音链路

更新：真实语音入口已改为本地录音/播放、服务器 ASR/TTS，通过 SSH 传输。配置后运行 `uv run --extra speech-client python -m speech.real_demo`；服务器无需声卡，本地无需模型。部署与验证见 [真实语音说明](../docs/REAL_SPEECH.md)。以下章节仍描述 P1/P2 模拟链路，不代表真实适配器的状态。

当前完成 P1/P1.1 输入和 P2 输出的内部模拟实现，不连接麦克风、扬声器或网络模型。TTS 生成确定性 PCM WAV 测试音调，不生成真实人声；播放器读取并校验音频，以其时长模拟播放进度。

## 输入

`SpeechInputService(recorder, asr, gateway)` 注入现有 `AudioRecorder`、`ASRProvider` 和 `TeamGateway`。服务拥有注入的录音器，关闭服务时会关闭录音器。

- `await start(task_id, recording_id, max_duration_s, timeout_s=30)`：开始录音；最大时长从录音启动计时，识别期限从录音结束后计时。
- `await finish(recording_id)`：主动结束并等待识别，返回 `UserInput | None`。
- `await wait_finished(recording_id)`：等待自动结束后的识别结果。
- `await cancel(recording_id)`：取消当前会话并等待清理，不取消核心教学任务。
- `await close()`：关闭全部会话，重复调用等待同一关闭作业。

每个录音编号在同一个服务实例内只允许 start 一次。重复 finish 使用同一个音频结果和识别作业。非最终或空转写返回 None；有效文本只通过 TeamGateway 提交，由它校验任务、去重并分配 input_id。已取消任务的迟到文本由门面拒绝，异常保留给等待方。

## 输出

`SpeechOutputService(tts, player)` 注入现有 `TTSProvider` 与 `AudioPlayer`。服务拥有合成作业和播放器；TTS Protocol 没有 close 方法，合成通过取消所属协程清理。

- `await start(playback_id, text, voice_id=None, timeout_s=30)`：开始输出，合成和播放共用期限。一次只允许一个活动输出。
- `await wait_finished(playback_id)`：返回现有 `PlaybackState`。合成失败、执行超时和停止确认失败抛出原始异常；播放设备失败返回 failed 状态。
- `await stop(playback_id)`：取消合成或停止播放。尚未播放时返回 None；已经播放时返回实际播放终态。自然结束仍是 completed，中途停止是 stopped。
- `await close()`：取消作业、确认播放停止并关闭播放器。

同一个实例不重用 playback_id。内部 get_stage() 仅诊断编排阶段，播放器 get_state() 返回实际 PlaybackState，不存在另一套公开播放状态模型。

## 装配示例

```python
device = AudioDeviceLease()
recorder = MockAudioRecorder(evidence, device)
player = MockAudioPlayer(evidence, device)
input_service = SpeechInputService(recorder, MockASRProvider(), gateway)
output_service = SpeechOutputService(MockTTSProvider(evidence), player)

await input_service.start(task_id, "recording-1", max_duration_s=15)
user_input = await input_service.finish("recording-1")
await output_service.start("playback-1", "这是调用方提供的测试回答")
playback = await output_service.wait_finished("playback-1")
await input_service.close()
await output_service.close()
```

示例中的 evidence、gateway、task_id 由现有核心应用提供。共享同一个 AudioDeviceLease 才会实现录音与播放互斥；设备占用时立即拒绝新操作。独立默认实例用于单组件测试。生产装配尚未接入。

归属：语音层定义并维护 AudioDeviceLease，应用装配层创建和共享实例，录音/播放适配器取得与释放占用。TTS 合成不占用设备。该方案仅覆盖单进程、同一事件循环中的模拟设备。对应合同的实现事实及待审核说明见 `docs/MODULE_CONTRACT.md` 的“音频互斥归属补充”，不代表生产互斥或合同审核已经完成。

## 取消与边界

取消结果等待者会取消对应语音作业并等待资源释放。cancel/stop/close 的等待者被取消时，清理任务仍会继续；调用方在清理完成后收到 CancelledError。并发重复请求等待同一个清理任务，不反复中断内部释放。

合成后的迟到结果通过内部取消标志拦截。清理依赖异步适配器最终退出；没有强制终止不合作硬件线程的能力。错误会保留并传播，不将停止失败当作成功。

音频证据仍使用现有内存 EvidenceStore，关闭释放任务和模拟设备占用，不删除已登记证据。证据容量、存储策略和共享模型均未修改。

主应用已可选装配 AudioAdapters，RobotHost 自动将回答交给 Runtime 的 SpeakSkill；任务资源屏障统一等待录音/识别停止，输出取消由动作生命周期负责。真实设备、离线 ASR 和系统人声合成已有独立适配器及演示，尚未装配到主应用。独立服务须自行管理资源，只有注册到同一个 TeamGateway 的资源才参与任务取消。HTTP/SSE 尚未实现；P3 前端模拟版本位于 `frontend/`，尚未连接本 Python 语音链路。接口合同仍待负责人审核。

## 验证

可运行的跨输入输出装配示例：`uv run python -m speech.demo`。它通过真实的内部 TeamGateway 输入队列交接模拟转写，再播放调用方提供的固定测试回答；不会生成教学回答或发出声音。核心交接条件与未完成项见 [内部联调及交接清单](../docs/SPEECH_DISPLAY_INTEGRATION.md)。

```bash
uv run python -m unittest tests.test_speech_input tests.test_speech_output -v
uv run python -m unittest tests.test_speech_integration -v
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run python -m unittest discover -s tests -v
```

并发测试使用事件固定清理/取消顺序，检查状态、调用次数、输入数量及测试兜底关闭前的任务和占用释放。

跨服务测试还覆盖连续两轮输入输出、设备占用失败后的恢复，以及关闭一侧服务时不释放另一侧服务正在使用的租约。

核心闭环演示：`uv run python -m app.robot_audio_demo`；不是上述固定回复的组件级 `speech.demo`。语音启动拒绝终结任务；启动与取消交错时先释放部分启动的录音，再确认任务终态。

## GPT-SoVITS 训练音色（Jetson 部署记录，2026-10-21）

- 客户端：`providers/tts/gpt_sovits.py`，设置 `SPEECH_TTS_ENGINE=gpt-sovits` 及 `.env.example` 中的 `SPEECH_GPTSOVITS_*` 后由 `app.audio.build_tts` 装配，随应用启动、关闭。
- 服务端：Jetson `~/opt/GPT-SoVITS`，`./run_api.sh` 启动 api_v2，只监听 `127.0.0.1:9880`（接口可读取任意参考音频路径，不得开放到局域网）。音色 `cf`（v2Pro）：GPT `GPT_weights_v2Pro/cf-e15.ckpt`，SoVITS `SoVITS_weights_v2Pro/cf_e8_s112.pth`，参考音频 `refs/cf_ref.wav` 及同名 `.txt` 原文。训练在 3080 主机完成。
- 部署要点：
  - PyTorch 用官方 `2.10.0+cu130` aarch64 版（含 sm_80 内核，可在 Orin sm_87 运行）；Jetson AI Lab 的 sbsa/cu130 版只含 sm_110/sm_121，在 Orin 上报 “no kernel image”。
  - `run_api.sh` 设置 `TORCH_CUDNN_V8_API_DISABLED=1`：PyTorch 2.10/2.11 + cuDNN 9 新接口每次卷积都在 CPU 上重新规划且不缓存，单句合成会从几秒变成一分钟以上（3080 与 Jetson 均复现）。
  - torchaudio 2.10 读取音频需要 torchcodec，而 torchcodec 0.10 只支持 FFmpeg 4–7，Jetson 为 FFmpeg 8；`api_jetson.py` 以 soundfile 替换 `torchaudio.load` 后运行原版 api_v2，不修改 GPT-SoVITS 仓库代码。
  - v2Pro 需要额外安装 `resampy`。
- 实测（Jetson Orin NX 8GB）：首句含预热约 29 s；之后 2.5–4.9 s 音频耗时 3.9–7.8 s。服务加载后整机可用内存约 0.5 GB，**不能与 `go2-unifolm.service`（llama-server 视觉模型）同时运行**；后者为 systemd 用户服务，开机自启，GPT-SoVITS 目前需手动启动。
