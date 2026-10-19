# 中文离线语音合成方案对比

记录日期：2026-10-19。范围：比赛机器人播放链路的离线中文 TTS 选型，比较合成延迟、流式能力、资源占用与许可证。

本记录只覆盖合成侧测量。ESP32-S3 传输与播放链路尚未实现，端到端首包延迟需在硬件联调后复测。

## 1. 背景约束

- 比赛机器主控为 ESP32-S3-WROOM-1-N8R8（PCB v0.5 PCBA），带 I2S 麦克风与功放，**无法在本机运行语音合成**。
- 合成必须在服务器或随行 Mac 上执行，ESP32 只负责接收音频并播放。
- 因此「首包延迟」由两段构成：合成侧出首块音频的时间 + 网络传输到 ESP32 的时间。本记录只测前一段。
- Edge 神经语音音质接近真人，但在线服务依赖公网，不作为比赛方案，仅作音质对标上限。

## 2. 候选方案

| 方案 | 版本 | 形态 | 备注 |
| --- | --- | --- | --- |
| sherpa-onnx VITS | 1.13.8 | 进程内常驻 Python API | 与 SenseVoice 识别同框架，`speech` extra 已含依赖 |
| Piper | piper-tts 1.8+ | 进程内常驻 Python API 或子进程 | `speech-server` extra 已含依赖，服务器部署过 |
| 系统合成 | macOS `say` / Linux `espeak-ng` | 每次调用启动子进程 | 当前 `ROBOT_AUDIO=real` 生产路径 |

模型：

| 方案 | 中文模型 | 体积 |
| --- | --- | --- |
| sherpa-onnx | `vits-zh-hf-fanchen-C`（含 lexicon、tokens、jieba dict） | 143 MB（模型 116 MB + 词典） |
| Piper | `zh_CN-huayan-medium` | 61 MB |
| 系统合成 | 系统内置音色 Tingting | 无 |

## 3. 测量环境

macOS（Apple Silicon），Python 3.12，sherpa-onnx 1.13.8，piper-tts 1.8+。每档文本重复 3 次取中位数，模型加载不计入单句耗时。全部为整句合成，未做分句切分。

文本档位：短句 2 字（控制指令）、中句 7 字（安全问答）、长句 27 字、回答 28 字（教学回答典型长度）。

## 4. 结果

| 方案 | 模型加载 | 短句 2 字 | 中句 7 字 | 长句 27 字 | 回答 28 字 | 实时率 |
| --- | --- | --- | --- | --- | --- | --- |
| sherpa-onnx VITS | 348 ms | 270 ms | 505 ms | 1402 ms | 1572 ms | 0.19–0.31 |
| Piper（进程内） | 530 ms | 24 ms | 40 ms | 127 ms | 151 ms | 0.02 |
| 系统 say | 无（每句启动进程） | 694 ms | 726 ms | 727 ms | 830 ms | 0.11–0.93 |

「长句 / 回答」两档的首块延迟与整句耗时不同：

| 方案 | 长句首块 | 长句整句 | 回答首块 | 回答整句 |
| --- | --- | --- | --- | --- |
| sherpa-onnx VITS | 581 ms | 1402 ms | 444 ms | 1572 ms |
| Piper | 127 ms（无分块） | 127 ms | 151 ms（无分块） | 151 ms |
| 系统 say | 727 ms（无分块） | 727 ms | 830 ms（无分块） | 830 ms |

同句音频时长（28 字）：Piper 7.10 s、sherpa 7.25 s、系统 say 6.34 s。三者语速未统一，实时率已按各自音频时长折算。

## 5. 关键发现

1. **Piper 合成最快**：整句 24–151 ms，实时率 0.02，比系统合成快约 5–30 倍。模型小、ONNX 推理路径短。
2. **sherpa-onnx 支持真流式**：长句与回答的首块在整句完成前的约三分之一处到达（581 ms 对 1402 ms、444 ms 对 1572 ms），整句虽慢于 Piper，但首块并不慢很多。
3. **Piper 与系统合成都不分块**：`PiperVoice.synthesize()` 虽返回生成器，实测整句只产出一个音频块；系统 `say` 是子进程，天然无增量输出。因此两者首包等于整句完成。
4. **sherpa-onnx 1.13.8 的 callback 语义与文档相反**：文档写明返回非零停止生成，实测返回 0 会中断（只得到首个分块，进度 0.33），返回非零才继续。使用流式回调必须按实测语义编写，否则会静默截断音频。
5. **现有生产路径每次都重建合成进程**：`SystemTTSProvider` 每句启动 `say` 子进程（约 700–830 ms）；`PiperTTSProvider` 每句启动 `python -m piper` 子进程，额外承担约 0.5 s 模型加载。若选 Piper，必须改为进程内常驻，否则优势会被进程启动与模型加载吃掉。
6. **同进程先后加载多个 ONNX 方案时，退出阶段可能打印析构异常**（`recursive_mutex lock failed`），退出码仍为 0，测量结果不受影响。分方案单独运行可避免。

## 6. 许可证

| 组件 | 许可证 | 风险 |
| --- | --- | --- |
| Piper 框架（piper-tts） | MIT | 低 |
| `zh_CN-huayan-medium` 音色 | `piper-voices` 元数据未声明音色级许可 | 需确认训练数据来源与再分发条件 |
| sherpa-onnx 框架 | Apache-2.0 | 低 |
| `vits-zh-hf-fanchen-C` 模型 | 模型仓库未声明许可证 | **许可不明**，比赛使用前需确认 |
| 系统 `say` 音色 | 随 macOS 提供 | 仅限 macOS，跨平台不可用；分发受限 |

两个中文模型都缺少明确的模型级许可声明，这是选型中唯一无法由技术数据解决的问题，需要负责人确认合规口径后再定方案。

## 7. 音质试听

同一句子（28 字）的三个方案样本已生成，保存在仓库外：

- `~/speech_eval/tts_samples/sherpa_vits.wav`
- `~/speech_eval/tts_samples/piper_huayan.wav`
- `~/speech_eval/tts_samples/system_tingting.wav`

音质为主观指标，未在本记录中评分，需人工试听后确认。

## 8. 结论与建议

- **合成速度**：Piper 明显领先，整句延迟可压到 200 ms 以内；sherpa-onnx 整句偏慢，但具备流式首块优势。
- **部署成本**：sherpa-onnx 与现有识别同框架、依赖已在 `speech` extra，服务器零新增依赖；Piper 需要 `speech-server` extra 与 61 MB 模型，模型体积约为前者四成。
- **建议**：若比赛现场随行 Mac 或服务器可部署，优先评估 **Piper 进程内常驻**；若倾向复用现有依赖且需要流式首包，选 **sherpa-onnx VITS**。系统合成只作基线，不作为交付方案。
- **前置条件**：两个候选的模型许可证都需要负责人确认；未确认前不写入默认配置。
- **若采用流式播放**：需要扩展 `TTSProvider.synthesize()` 的返回形态（当前返回完整 `AudioReference`），属于接口合同变更，须先更新合同并审核，不在本轮实施。

## 9. 未验证

- Linux 服务器上的实际延迟（本轮全部在 macOS Apple Silicon 测得）。
- ESP32-S3 传输与播放链路的首包延迟、缓冲策略。
- 真人听辨与长时间连续合成的稳定性。
- 多句并发与打断场景下的资源占用。

## 10. 复现

```bash
# 三个方案一起测量（注意同进程析构提示，分方案运行更干净）
uv run --extra speech --extra speech-server python -m tools.bench_tts \
  --sherpa-model ~/speech_eval/tts_models/sherpa-vits \
  --piper-model ~/speech_eval/tts_models/zh_CN-huayan-medium.onnx \
  --repeat 3 --output ~/speech_eval/tts_bench.md

# 单方案
uv run --extra speech --extra speech-server python -m tools.bench_tts \
  --engines sherpa --sherpa-model ~/speech_eval/tts_models/sherpa-vits
```

模型与试听样本均保存在仓库外，不入版本控制。
