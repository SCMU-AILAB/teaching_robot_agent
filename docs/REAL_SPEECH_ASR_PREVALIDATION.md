# 识别链路合成语音预验证记录

记录日期：2026-10-19。范围：真人验收开始前，用合成语音验证识别链路、评测脚本与引擎对比口径。

**本记录不是真人验收结论。** 合成语音缺少呼吸、口误、口语化停顿、远场混响与口音多样性，只能证明链路可用、引擎行为一致，不能替代现场录音验收。

## 1. 验证内容

| 项目 | 构造方式 |
| --- | --- |
| 语料 | 20 句，覆盖知识库 7 类与 4 条控制指令，与验收规划一致 |
| 合成音频 | macOS `say`（16 kHz 单声道 16-bit）与微软 Edge 神经语音（经 `afconvert` 转 16 kHz 单声道） |
| 口音差异 | macOS Meijia（台湾腔）作为说话风格差异样本 |
| 背景噪音 | 对 Tingting 组叠加白噪声，信噪比约 10 dB |
| 独立校验 | sherpa-onnx 公开真实录音样本 `test_wavs/zh.wav`（hf-mirror 获取） |

合成音频与录音文件均保存在仓库外，不纳入版本控制。

## 2. 结果

统计口径：字错率为归一化编辑距离除以参考字数，已统一中英文数字写法；耗时为单条识别耗时，模型常驻加载约 2.4 秒不计入。

| 音色 / 条件 | 引擎 | 字错率 | 平均耗时 | 最大耗时 |
| --- | --- | --- | --- | --- |
| Edge Xiaoxiao（女声） | SenseVoice int8 | 0.0% | 35 ms | 43 ms |
| Edge Yunxi（男声） | SenseVoice int8 | 0.0% | 39 ms | 55 ms |
| Edge Yunyang（播音男声） | SenseVoice int8 | 0.6% | 37 ms | 58 ms |
| macOS Tingting | SenseVoice int8 | 0.6% | 33 ms | 51 ms |
| macOS Meijia（台湾腔） | SenseVoice int8 | 1.2% | 32 ms | 46 ms |
| macOS Tingting + 噪声 10 dB | SenseVoice int8 | 1.8% | 31 ms | 46 ms |
| Edge Xiaoxiao | Whisper base int8 | 34.4%（繁体） | 341 ms | 451 ms |
| Edge Xiaoxiao | Whisper base int8，繁转简后 | 11.0% | — | — |

每组 20 条、参考 163 字。Whisper 组耗时为原始输出口径；繁转简仅用于对比字错率，不改变耗时统计。

公开真实录音样本识别为「开饭时间早上9点至下午5点。」，与样本内容一致，链路独立校验通过。

## 3. 方法论修正：数字归一化

首轮评测中，`八四消毒液` 被识别服务 ITN 输出为 `84消毒液`，逐字符比对时被判为两处替换，导致全部合成组字错率一致偏高（约 1.2 个百分点）。修正方式：`tools/eval_asr.py` 的归一化阶段先做 NFKC 兼容归一（全角转半角），再把阿拉伯数字逐位映射为中文数字，使参考文本与识别文本在同一书写形式下比对。

修正前后对比：

| 音色 / 条件 | 修正前 | 修正后 |
| --- | --- | --- |
| Edge Xiaoxiao | 1.2% | 0.0% |
| Edge Yunxi | 1.2% | 0.0% |
| Edge Yunyang | 1.8% | 0.6% |
| macOS Tingting | 1.8% | 0.6% |
| macOS Meijia | 2.5% | 1.2% |
| macOS Tingting + 噪声 | 3.1% | 1.8% |

已知局限：逐位映射不覆盖单位读法（如 `10` 与 `十`）。语料中仅 `八四`、`第五课` 等逐位场景，当前口径够用；若后续语料引入 `十点`、`一百` 等读法，需在归一化中补充数字读法转换。

## 4. 观察

1. 修正数字口径后，SenseVoice 在合成语音上的字错率为 0%–1.8%，单条识别 31–39 ms，加噪至 10 dB 仅升至 1.8%。
2. 剩余错误均为同音字混淆：`大点声`→`大点生`、`别动了`→`憋动了`、`水`→`你`、`和`→`焊`。这类错误在真人录音中同样会出现，属于真实识别损失，不应从字错率中剔除。
3. Whisper base 对中文输出繁体（`熱水壺`→`熱水湖`），原始字错率 34.4% 主要是字形差异；繁转简后 11.0%，仍明显高于 SenseVoice，且单条耗时约为 SenseVoice 的 9 倍。
4. 合成语音之间的差异（音色、口音、加噪）对 SenseVoice 影响有限，但远场与真实噪音无法由合成方式可靠模拟，不能据此推断现场表现。

## 5. 对后续决策的意义

- **引擎选型**：在合成语音条件下，SenseVoice 在字错率与耗时两项上均优于 Whisper base，支持默认常驻 SenseVoice 的现状；该结论需在真人录音上复核后才能作为最终依据。
- **评测口径**：数字归一化已修正，后续真人验收数据统一使用修正后口径，可与本记录横向比较。
- **不能推出的结论**：不代表现场识别准确率达标，不代表远距离与噪音条件可用，不替代真人验收。

## 6. 复现

```bash
# 单引擎评测
uv run --extra speech python -m tools.eval_asr \
  --references ~/speech_eval/refs_EDGE_Xiaoxiao.csv \
  --audio-dir ~/speech_eval/audio \
  --engines sensevoice \
  --sensevoice-model models/sensevoice-small

# 双引擎对比（需 faster-whisper 模型目录）
uv run --extra speech python -m tools.eval_asr \
  --references ~/speech_eval/refs_EDGE_Xiaoxiao.csv \
  --audio-dir ~/speech_eval/audio \
  --engines sensevoice,whisper \
  --sensevoice-model models/sensevoice-small \
  --whisper-model <faster-whisper 模型目录>
```

参考文本 CSV 由 `filename,category,text` 三列构成，文件名需与音频目录中的 WAV 一致。
