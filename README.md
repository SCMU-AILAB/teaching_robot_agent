# 家庭学习陪伴与安全教育机器人

围绕赛题提供通用机器人任务框架：语音/文字输入、物体观察、移动讲解和安全知识问答。默认流程不创建课程，不要求出题评分；固定课堂是独立可选模式。

## 当前主链路

```text
文字 / 最终语音转写 → TeamGateway → RobotHost → EmbodiedAgent
    → LangChain + 本地 Qwen → 查询知识 / 观察现场 / 提交动作
    → Skill Runtime → RobotAdapter → 模拟器或未来官方 SDK
    → 执行验证 → 唤醒宿主继续决策 → 返回文本与任务状态
```

观察工具返回场景摘要、物体标签与相对方位（左侧/中间/右侧/不确定；主控制台当前装配的模拟感知不返回物体，需接入真实感知才有物体），并附最多 3 条关联安全资料及来源；无相关条目时明确返回“资料库没有相关条目”。同一任务内保留最近 4 轮对话记忆，用于“它”“那个”等追问；记忆随任务结束丢弃，移动后的旧观察焦点标为“移动前”。

主应用通过 `RobotApplication(robot, perception, model, knowledge)` 注入依赖。`RobotContextBuilder` 提供任务、设备能力、动作记录和最近观察；`KnowledgeService` 独立于教学会话。模型不直接操作设备，停止不等待模型返回。

架构、模块边界及验收项见 [赛题框架](docs/COMPETITION_ARCHITECTURE.md)。

## 启动

```bash
uv sync --locked
# 在环境中设置 OLLAMA_BASE_URL，必要时先建立 SSH 隧道。
uv run main.py
```

控制台直接输入“查询热水安全知识”“看看桌上有什么”“模拟左转 0.1 弧度后再观察”等任务。`/finish` 验收当前任务，`/stop` 停止当前任务，`/quit` 退出。未结束时继续输入会沿用当前任务，忙碌期间普通请求被拒绝，停止命令仍可处理。当前控制台支持 macOS/Linux 终端。

**默认模型是真实本地 Qwen，机器人和感知均为模拟。** 默认仅文字输出；设置 `ROBOT_AUDIO=simulated` 后，回答通过 Runtime 执行模拟播报（不输出真实声音）；设置 `ROBOT_AUDIO=real` 后使用本机麦克风、进程内常驻 Whisper 识别、系统合成和扬声器，控制台用 `/record` 开始录音、`/done` 结束并识别。主入口没有连接真实摄像头。跨任务长期对话记忆尚未实现。所有真实部署地址与凭据由环境提供，程序不自动读取 `.env`。

主入口已从旧动作演示改为机器人控制台。原演示使用独立模块：

```bash
uv run python -m app.demo --scenario all
uv run python -m app.robot_demo
uv run python -m app.model_demo vision /path/to/photo.png '图中有什么？'
uv run python -m app.classroom_demo
```

第一项为离线动作生命周期测试；第二项验证模拟前进、转向、再前进；第三项是真实 VLM 读取静态图；第四项为可选固定课堂。模型相关入口均需配置本地服务地址。

## 关键代码

| 模块 | 责任 |
| --- | --- |
| `app/robot_application.py` | 通用应用装配和生命周期 |
| `agent/robot_host.py` | 输入去重、模型决策、等待动作、停止和任务结束 |
| `agent/embodied_agent.py` | 有界工具循环，模型参数验证 |
| `agent/context.py` | 机器人上下文；课程上下文独立保留 |
| `agent/tools.py` | 按任务绑定工具，课程工具可选 |
| `education/knowledge_service.py` | 家庭学习及安全知识资料，与课程状态解耦 |
| `perception/interfaces.py` | 相机、视觉提供者和感知服务契约 |
| `providers/ollama_vision.py` | 真实本地 VLM 图片校验和响应解析 |
| `runtime/` | 动作生命周期、任务状态、事件广播 |
| `robot/base.py`、`robot/factory.py` | 与厂商无关的设备能力、接口与构造器注册 |
| `skills/robot_registry.py` | 按设备能力开放相对移动与转向 |
| `speech/interfaces.py` | 录音、识别、合成、播放契约 |
| `agent/classroom_host.py` | 可选课程模式，不是默认机器人入口 |

## 执行规则

- 正常动作进入 Runtime，接受不表示完成。执行后核对位姿、静止状态与证据。
- 动作失败、超时或取消不会触发模型自动重试；无法确认停止时 Runtime 阻止后续运动。
- 通用宿主最多续接三个动作，达到上限会停止，不把上限当作目标完成。
- 每次运动使旧观察失效；已有场景的任务在动作成功后由宿主强制重新观察，再续接模型。观察时间、模拟标记及证据进入上下文。
- 模型文字不自动将任务设为 completed；由操作者 `/finish` 或确定性验收流程调用 `finish_task`，且所有实际动作必须成功结束。
- 物体讲解与安全提醒只来自模型对画面的观察与资料库的人工示例条目，不是现场风险检测；关联条目不在资料库时，模型须说明资料缺失。对话记忆有界（4 轮，用户原话 200 字、回答 300 字），用户原话以数据而非指令渲染。
- 成功输入按编号去重；处理失败先取消任务，禁止无条件重放。
- 应用关闭时取消任务，再释放感知与设备。主应用已装配 ActionEventHub 持续消费 Runtime 事件，外部通过有界订阅获取事件。

当前设备动作只有模拟相对移动和转向，没有真实导航、避障或硬件急停。导航只定义接口，模拟位移不能被称为导航到物体。真实设备需按反馈精度、状态新鲜度及停止行为独立验收。

## 团队接入与规范

- [设备适配接口](docs/ROBOT_ADAPTER.md)：拿到主办方 SDK 后实现 RobotAdapter。
- [本地模型配置及调研](docs/LOCAL_MODELS.md)：Qwen 默认、宇树 VLM 对照、服务器联调结果。
- [模块协作合同](docs/MODULE_CONTRACT.md)：共享媒体、感知、语音接口及证据存储。
- [接口文档 v1.0](API_CONTRACT.md)：仓库全部对外接口（HTTP API、底盘串口协议、外部推理服务）的唯一依据，前端按此实现；后端由 `uv run python -m app.robot_server` 启动，调试页面 `/api/v1/docs`。
- [Jetson 设备端部署指南](docs/JETSON_DEVICE_GUIDE.md)：Jetson 上安装、配置（`deploy/jetson/robot.env.example`）、到 3080 的 SSH 隧道与 systemd 常驻服务（`deploy/jetson/*.service`）、验收清单。
- [编码规范](docs/CODING_SPEC.md)：代码由负责人审核；当前按用户指示直接在 main 修改。

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run python -m unittest discover -s tests -v
```

basedpyright 要求零错误、零警告。记录、输入去重和教学状态目前均在内存，退出后不保留。

内存保留限制：任务与动作各保留 4096 条，满额时清理最早的已结束任务及其动作，未结束的任务与动作不清理，全部未结束才拒绝；宿主输入去重保留最近 4096 条，转写去重在任务结束时清理；输入队列 1024 条。证据默认最多 64 MiB：观察帧由 TeamGateway 按任务登记，任务验收或取消后释放未被其他进行中任务引用的帧；录音在识别结束、播报音频在播放器确认停止后由语音服务释放。主应用需向 `RobotApplication` 传入 `_evidence` 才启用回收。清理的记录不归档。

设备遥测 `updated_at` 必须是本机时间基准的真实采集 Unix 秒，不能以读取缓存的时间冒充采集时间。默认最大延迟两秒；到位反馈需晚于动作命令，停止反馈需晚于停止请求。接入设备时需完成时钟映射及对应确认测试。

## 核心语音闭环

离线运行：`uv run python -m app.robot_audio_demo`。使用已配置本地模型：追加 `--local-model`。默认演示用受控模型，明确标记模拟，不依赖服务器。

链路：模拟录音 → ASR → TeamGateway → EmbodiedAgent → RobotHost → `speak` 动作 → TTS → 播放确认。`RobotApplication` 接受 `AudioAdapters` 注入；真实设备实现沿用相同 Protocol，应用负责共享音频设备占用。

`start_recording` 与 `finish_recording` 是内部调用入口；转写只由服务入队一次。`cancel_task` 同时取消模型等待、Runtime 动作及本任务录音/识别，资源确认前保持 cancelling；停止失败记为 failed。录音进行中不能验收完成，取消一个任务不停止其他任务的录音。

主应用自动播报最终回答，最多 4000 字，自动播报动作预算 30 秒；失败时保留回答文字及失败动作，不自动重播。模型主动动作仍受原有三个连续动作上限约束，最终回答播报另计一次。语音输出的内部合成与播放默认也共用 30 秒期限。音频设备忙时明确失败，不偷偷抢占其他任务。

语音会话及输出服务各保留最多 4096 条记录；满额时清理已结束的旧记录，仍满才拒绝新作业，证据仍遵循共享存储容量。真实设备、ASR/TTS、人声播报和 Flutter 网络联调尚未完成。

## 本机真实语音（常驻识别）

`ROBOT_AUDIO=real` 时，`ResidentASRProvider` 在 `RobotApplication.start()` 加载一次识别模型（默认中文 SenseVoice-small，经 sherpa-onnx 在 CPU 运行；`SPEECH_ASR_ENGINE=whisper` 切回 Whisper），`close()` 释放；每次识别只做推理，不再为每段语音新起进程或 SSH 连接。同一模型串行推理；取消或超时会等原生推理结束后再传播，迟到结果丢弃。模型缺失时启动直接失败，不退回模拟识别，也不在运行时自动下载。

```bash
uv sync --locked --extra speech
# 一次性下载模型到 git 忽略的 models/（国内网络可设 HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1）
uv run --extra speech python -c 'from huggingface_hub import snapshot_download; snapshot_download("Systran/faster-whisper-base", local_dir="models/whisper-base", allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"])'
export ROBOT_AUDIO=real SPEECH_ASR_MODEL_PATH=models/whisper-base
uv run --extra speech main.py
```

2026-10-14 在 Apple M2 CPU（int8、base 模型）实测：模型加载约 0.6 秒；3.2 秒与 1.1 秒合成中文语音识别分别约 0.5–0.7 秒、0.4 秒。原远端方案（SSH 到服务器、每次新起进程）热状态约 2.6 秒。base 模型把“热水壶”识别为“热水湖”，需要更高准确率时可换 small 模型。远端 SSH 识别链路仍保留在 `speech.real_demo`。

2026-10-14 在服务器上用 72 段合成语音（6 种音色 × 12 句）对比：Whisper base 字错率 49.7%，SenseVoice-small 18.6%（自然音色 1%，CPU 每段约 0.13 秒），large-v3-turbo 13.1%（需 GPU）。加提示词会让 Whisper 输出提示原文或无关文本，已弃用。默认改为 SenseVoice：下载 `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17` 的 `model.int8.onnx`、`tokens.txt` 到 `models/sensevoice-small`，设 `SPEECH_ASR_MODEL_PATH=models/sensevoice-small`。尚未用真人录音验收。
