# 中文离线语音合成方案对比

记录日期：2026-10-19（修订：采样率与分块口径修正）。范围：比赛机器人播放链路的离线中文 TTS 选型，比较合成延迟、增量输出、内存占用与许可证。

**当前状态：Mac 合成侧初测完成，选型待补证。**

本记录只覆盖 macOS Apple Silicon 上的合成侧测量。服务器资源占用、ESP32-S3 传输与播放链路、合成到实际出声的端到端延迟均未测量，不能据此外推。

## 1. 背景约束

- 比赛机器主控为 ESP32-S3-WROOM-1-N8R8（PCB v0.5 PCBA），带 I2S 麦克风与功放，**无法在本机运行语音合成**。
- 合成必须在服务器或随行 Mac 上执行，ESP32 只负责接收音频并播放。
- 因此「首包延迟」由两段构成：合成侧出首块音频的时间 + 网络传输到 ESP32 的时间。本记录只测前一段。
- Edge 神经语音音质接近真人，但在线服务依赖公网，不作为比赛方案，仅作音质对标上限。

## 2. 候选方案

| 方案 | 版本 | 形态 | 备注 |
| --- | --- | --- | --- |
| sherpa-onnx VITS | 1.13.8 | 进程内常驻 Python API | 与 SenseVoice 识别同框架，`speech` extra 已含依赖 |
| Piper | piper-tts 1.8.0 | 进程内常驻 Python API 或子进程 | `speech-server` extra 已含依赖，服务器部署过 |
| 系统合成 | macOS `say` / Linux `espeak-ng` | 每次调用启动子进程 | 当前 `ROBOT_AUDIO=real` 生产路径 |

模型：

| 方案 | 中文模型 | 体积 | 输出采样率 |
| --- | --- | --- | --- |
| sherpa-onnx | `vits-zh-hf-fanchen-C`（含 lexicon、tokens、jieba dict） | 143 MB（模型 116 MB + 词典） | 16000 Hz |
| Piper | `zh_CN-huayan-medium` | 61 MB（模型 60 MB + 配置） | **22050 Hz** |
| 系统合成 | 系统内置音色 Tingting | 无 | 16000 Hz（按 `--data-format` 指定） |

## 3. 测量环境

macOS（Apple Silicon），Python 3.12，sherpa-onnx 1.13.8，piper-tts 1.8.0。每档文本重复 3 次取中位数，模型加载不计入单句耗时。三个方案分进程独立运行，内存为进程峰值增量（含解释器与推理运行时基线，非模型净增量）。

文本档位：短句 2 字（控制指令）、中句 7 字（安全问答）、多句 15 字（两句，用于观察分块）、长句 27 字、回答 28 字（教学回答典型长度）。

## 4. 结果

| 方案 | 模型加载 | 内存增量 | 短句 2 字 | 中句 7 字 | 多句 15 字 | 长句 27 字 | 回答 28 字 | 实时率 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sherpa-onnx VITS | 293 ms | 352 MB | 143 ms | 334 ms | 570 ms | 1194 ms | 1283 ms | 0.17–0.19 |
| Piper（进程内） | 486 ms | 241 MB | 25 ms | 41 ms | 72 ms | 123 ms | 131 ms | 0.03 |
| 系统 say | 0 ms（每句启动进程） | 子进程不计 | 663 ms | 659 ms | 671 ms | 692 ms | 706 ms | 0.11–0.89 |

音频时长与分块：

| 方案 | 短句 | 中句 | 多句 | 长句 | 回答 |
| --- | --- | --- | --- | --- | --- |
| sherpa 音频时长 | 0.80 s | 1.94 s | 3.04 s | 7.06 s | 7.36 s |
| sherpa 首块 / 块数 | 143 ms / 1 | 334 ms / 1 | 200 ms / 2 | 482 ms / 3 | 379 ms / 3 |
| Piper 音频时长 | 0.74 s | 1.49 s | 2.43 s | 4.59 s | 4.91 s |
| Piper 首块 / 块数 | 25 ms / 1 | 41 ms / 1 | 24 ms / 2 | 123 ms / 1 | 131 ms / 1 |
| 系统 say 音频时长 | 0.75 s | 1.50 s | 2.68 s | 6.44 s | 6.34 s |

Piper 的输出采样率为 22050 Hz，音频时长与实时率均按该采样率换算。此前按 16000 Hz 换算会把时长高估约 38%、实时率低估约 27%，该错误已修正。

## 5. 增量输出能力

两个引擎都支持增量输出，但粒度不同，不能简单概括为「只有 sherpa 支持流式」：

| 方案 | 分块粒度 | 实测表现 | 当前适配器是否利用 |
| --- | --- | --- | --- |
| sherpa-onnx VITS | 按文本分段/句切块 | 多句 2 块、长句与回答 3 块；首块在整句完成前到达（379 ms 对 1283 ms） | 尚无 TTS 适配器 |
| Piper | **按句切块**（句内不分块） | 单句 1 块；两句文本 2 块，首块 24 ms、整句 72 ms | 未利用：`PiperTTSProvider` 走子进程整段写 WAV |
| 系统 say | 无分块 | 每次子进程只产出完整 WAV | 未利用：整段写文件 |

补充事实：

- Piper 的 `synthesize()` 是按句生成，不是按音频帧流式；同一句内不会提前给出部分音频。
- `TTSProvider.synthesize()` 当前返回完整 `AudioReference`，两个引擎的分块能力都未进入接口，要实现边合成边播放必须先扩展合同。

## 6. 关键发现

1. **Piper 合成最快**：整句 25–131 ms，实时率 0.03，比系统合成快 5–25 倍。
2. **sherpa-onnx 首块最早**：长句与回答首块 379–482 ms，比整句完成早约 0.8 s；Piper 单句虽快但句内不给中间结果。
3. **sherpa-onnx 1.13.8 的 callback 语义与文档相反**：文档写明返回非零停止生成，实测返回 0 会中断（只得到首个分块），返回非零才继续。按文档编写会静默截断音频。
4. **现有生产路径每次都重建合成进程**：`SystemTTSProvider` 每句启动 `say` 子进程（约 660–710 ms）；`PiperTTSProvider` 每句启动 `python -m piper` 子进程，额外承担约 0.5 s 模型加载。若选 Piper，必须改为进程内常驻，否则优势会被进程启动与模型加载吃掉。
5. **同进程先后加载多个 ONNX 方案时，退出阶段可能打印析构异常**（`recursive_mutex lock failed`），退出码仍为 0，测量结果不受影响。分方案单独运行可避免。

## 7. 许可证

| 组件 | 许可证 | 依据 | 风险 |
| --- | --- | --- | --- |
| piper-tts 1.8.0（Python 包） | **GPL-3.0-or-later** | 已安装包元数据 `License: GPL-3.0-or-later` | **高**：GPL 具有传染性，分发包含该包的软件需按 GPL 提供源码 |
| `zh_CN-huayan-medium` 音色 | `piper-voices` 元数据未声明音色级许可 | 模型仓库元数据 | 需确认训练数据来源与再分发条件 |
| sherpa-onnx 1.13.8 框架 | Apache-2.0 | 上游仓库 | 低 |
| `vits-zh-hf-fanchen-C` 模型 | 模型仓库未声明许可证 | HF 仓库元数据 | **许可不明** |
| 系统 `say` 音色 | 随 macOS 提供 | 系统许可 | 仅限 macOS；分发与跨平台受限 |

三个候选都存在许可问题：Piper 的运行时是 GPL-3.0-or-later，两个中文模型都没有模型级许可声明。若比赛只在内网自用、不分发软件，GPL 的实际约束较小；但只要对外分发或在其他队伍复用的场景，就需要按 GPL 处理或替换运行时。**该口径必须由负责人确认后才能写入默认配置。**

## 8. 音质试听

同一句子（28 字）的三个方案样本已生成，保存在仓库外：

- `~/speech_eval/tts_samples/sherpa_vits.wav`
- `~/speech_eval/tts_samples/piper_huayan.wav`
- `~/speech_eval/tts_samples/system_tingting.wav`

注意：Piper 与 sherpa 的输出采样率不同，比较前应统一重采样，否则只是听感差异而非音质差异。音质为主观指标，未在本记录中评分。

## 9. 结论与建议

- **合成速度**：Piper 明显领先，整句可压到 150 ms 内；sherpa-onnx 整句偏慢，但首块更早，适合需要边合成边播的场景。
- **增量输出**：两者都有按句/按段切块的能力，粒度不同；当前适配器都没有利用，流式播放不是现成能力。
- **许可证**：Piper 运行时为 GPL-3.0-or-later，两个中文模型许可不明，这是选型的主要阻塞项，不是技术指标问题。
- **建议**：在许可证口径确认前，不把任何方案写入默认配置。技术上优先评估 **Piper 进程内常驻**（速度、内存均优）；若许可不可接受，再评估 **sherpa-onnx VITS**（Apache-2.0 框架、与识别同依赖）并单独解决模型许可。
- **若采用流式播放**：需要扩展 `TTSProvider.synthesize()` 的返回形态，属于接口合同变更，须先更新合同并审核。

## 10. 未验证

- Linux 服务器上的合成延迟与内存占用（本轮全部在 macOS Apple Silicon 测得）。
- ESP32-S3 传输与播放链路：首包到达功放的实际延迟、缓冲策略、网络抖动影响。
- 真人听辨与长时间连续合成的稳定性。
- 多句并发与打断场景下的资源占用。

## 11. 复现

```bash
# 单方案（推荐：内存与退出行为更干净）
uv run --extra speech --extra speech-server python -m tools.bench_tts \
  --engines piper \
  --piper-model ~/speech_eval/tts_models/zh_CN-huayan-medium.onnx \
  --repeat 3 --output ~/speech_eval/tts_bench_piper.md

uv run --extra speech --extra speech-server python -m tools.bench_tts \
  --engines sherpa \
  --sherpa-model ~/speech_eval/tts_models/sherpa-vits \
  --repeat 3 --output ~/speech_eval/tts_bench_sherpa.md

# 三方案一起运行（内存增量会累积，仅用于快速对比）
uv run --extra speech --extra speech-server python -m tools.bench_tts \
  --sherpa-model ~/speech_eval/tts_models/sherpa-vits \
  --piper-model ~/speech_eval/tts_models/zh_CN-huayan-medium.onnx \
  --repeat 3
```

模型与试听样本均保存在仓库外，不入版本控制。
