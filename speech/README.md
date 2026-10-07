# 内部模拟语音链路

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

## 取消与边界

取消结果等待者会取消对应语音作业并等待资源释放。cancel/stop/close 的等待者被取消时，清理任务仍会继续；调用方在清理完成后收到 CancelledError。并发重复请求等待同一个清理任务，不反复中断内部释放。

合成后的迟到结果通过内部取消标志拦截。清理依赖异步适配器最终退出；没有强制终止不合作硬件线程的能力。错误会保留并传播，不将停止失败当作成功。

音频证据仍使用现有内存 EvidenceStore，关闭释放任务和模拟设备占用，不删除已登记证据。证据容量、存储策略和共享模型均未修改。

尚未实现真实设备、真实 ASR/TTS、人声播报、教学回答自动连接输出、核心任务对全部语音资源的统一取消、SpeechSkill、HTTP/SSE 和前端。接口合同仍待负责人审核。

## 验证

```bash
uv run python -m unittest tests.test_speech_input tests.test_speech_output -v
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run python -m unittest discover -s tests -v
```

并发测试使用事件固定清理/取消顺序，检查状态、调用次数、输入数量及测试兜底关闭前的任务和占用释放。
