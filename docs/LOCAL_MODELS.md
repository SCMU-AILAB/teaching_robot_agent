# 本地模型选型与接入

调研及联调日期：2026-10-04。当前选择 **Qwen3.5-9B**，复用服务器已有的 Ollama 模型 `qwen3.5:9b`。宇树 `unitree-unifolm-vlm:q8` 保留为视觉对照，可通过 `VLM_MODEL` 切换。

## 选型依据

| 项目 | 宇树 UnifoLM-VLM-Base | Qwen3.5-9B |
| --- | --- | --- |
| 官方定位 | 通用图文问答及机器人数据训练，服务具身操作体系 | 原生视觉语言模型，支持通用理解及 Agent 场景 |
| 模型关系 | 官方 config 声明 Qwen2.5-VL 架构 | Qwen3.5 系列的 9B 模型 |
| 当前服务器版本 | 社区 GGUF 转换，Q8_0，文件约 8.95 GB | Ollama Q4_K_M，文件约 6.59 GB |
| 本机服务声明的能力 | completion、vision | completion、vision、tools、thinking |
| 当前用途 | 对照视觉识别；未验证原生工具调用 | 首选视觉服务与上层 LangChain 决策模型 |
| 官方模型许可 | 模型卡标注 CC BY-NC-SA 4.0 | 模型卡标注 Apache-2.0 |

这些是定位、部署及接入成本的比较，不构成同精度、同数据集的准确率排名。文件大小不是运行显存；当前 GPU 为 RTX 3080、20480 MiB 显存，视觉请求实际成功运行。先使用 4096 上下文、单请求，不据模型宣传的最大上下文直接配置几十万 token。

宇树确实公开了独立的 **UnifoLM-VLM-Base**，不能将它全部归类为 VLA。其 VLA/WMA 型号以动作预测、操作或世界建模为目标，不是当前教学问答服务的直接替代品。接宇树机器人 SDK 也不要求上层必须使用宇树模型。

首版复用同一个 Qwen 模型，代码中仍分别注入 Agent 和 VisionProvider；不常驻加载两个大模型。若后续验证宇树在特定操作场景更好，只需切换视觉提供者，不改变 Runtime。

官方来源：

- [宇树 UnifoLM-VLA 仓库与独立 VLM 权重列表](https://github.com/unitreerobotics/unifolm-vla)
- [宇树 VLM 模型卡](https://huggingface.co/unitreerobotics/Unifolm-VLM-Base)
- [宇树 VLM 配置](https://huggingface.co/unitreerobotics/Unifolm-VLM-Base/blob/main/config.json)
- [Qwen3.5 官方仓库](https://github.com/QwenLM/Qwen3.5)
- [Qwen3.5-9B 模型卡](https://huggingface.co/Qwen/Qwen3.5-9B)
- [Qwen3-VL 官方仓库](https://github.com/QwenLM/Qwen3-VL)：另一个可选系列，本次发现已有 Qwen3.5 后未重复下载。

## 已完成的服务器验证

使用同一张 512×384 合成图片：左侧红色圆形，右侧蓝色长方形，白色背景、无阴影。两次请求串行，温度 0、4096 上下文、最多 180 输出 token；关闭 thinking、请求后释放模型。

| 模型 | 单次端到端耗时，含加载 | 观察 |
| --- | --- | --- |
| 宇树 Q8 | 约 15.44 秒 | 颜色和左右正确；把长方形说成正方形，添加不存在的阴影 |
| Qwen3.5 Q4 | 约 12.42 秒 | 颜色和左右正确；形状写成“矩形（或正方形）”，仍不够确定 |

随后通过项目的 `OllamaVisionProvider`、JSON 输出约束和实际证据读取链路再次请求 Qwen，返回“左侧是一个红色的圆形，右侧是一个蓝色的矩形”。这仅验证基础图片及协议，不代表真实课堂准确率。后续应用真实摄像头采集的物体、中文教材、遮挡和模糊图片做同题多次比较，分别记录错误、幻觉、热启动延迟及峰值显存。

相同适配器切换到宇树后也成功返回符合 JSON 约束的观察，但仍称右侧为“方形”，并添加缺乏依据的边缘描述。Qwen 的 LangChain 教学联调日志确认实际执行了 `lookup_knowledge`，随后依据资料生成中文讲解。模型适配阶段的 47 项单元测试通过，Ruff 与格式检查通过，basedpyright 为零错误、零警告。

## 接入代码

- `providers/local_model.py`：从环境创建 LangChain `ChatOllama`；不在导入时连接，不修改全局代理；请求绕过环境代理，限制上下文和超时。
- `providers/ollama_vision.py`：实现已有 `VisionProvider.analyze()` 契约。读取 EvidenceStore，验证图片、尺寸和采集时间，严格解析 JSON；首版仅填 `summary`，不伪造对象框、关系或距离。
- `agent/langchain_tools.py`：把七个已有工具注册为 LangChain 工具。
- `agent/embodied_agent.py`：刷新统一上下文，执行有界工具循环；逐批校验工具参数与任务归属；学生答案必须匹配宿主输入。动作提交独占一批并返回 `pending_action_id`，宿主通过事件或 `wait_for_action()` 等待终态后再决策。
- `app/model_demo.py`：静态图片视觉联调和教学知识工具联调。

两类模型可分别配置，默认均为 `qwen3.5:9b`。宇树当前安装包未声明 tools 能力，切换到宇树时只设置 `VLM_MODEL`。

## 运行方式

先安装锁定依赖：`uv sync --locked`。应用不会自动读取 `.env`。

如果服务仅监听服务器回环地址，在自己的终端建立隧道。以下主机、用户名和端口均由本机环境提供，不写入仓库：

```bash
ssh -N -L "127.0.0.1:${LOCAL_MODEL_PORT}:127.0.0.1:${REMOTE_MODEL_PORT}" \
  "${MODEL_SSH_USER}@${MODEL_SSH_HOST}"
```

在另一个终端运行：

```bash
export OLLAMA_BASE_URL="http://127.0.0.1:${LOCAL_MODEL_PORT}"
export VLM_MODEL=qwen3.5:9b
export AGENT_MODEL=qwen3.5:9b

uv run python -m app.model_demo vision /path/to/photo.png '图中有什么？'
uv run python -m app.model_demo agent '请先查询圆形的教学资料，再向小学生解释。'
```

本次调试通过临时 SSH 隧道使用已有服务；没有改服务器服务配置，没有部署常驻 Agent，没有安装或下载新权重。联调入口的 Agent 使用模拟感知和模拟设备，未注册移动技能；视觉入口使用真实模型及静态文件，不能当作实时相机链路。

## 当前边界

- 同步修改仍留在工作区，待项目负责人审核。
- `ClassroomHost` 已串联 TeachingFlow 与 LangChain：规则出题评分、模型追问、等待动作终态、完成纯教学任务。运行 `uv run python -m app.classroom_demo` 查看装配示例；真实摄像头与音频仍待接入。
- 真实摄像头依然由感知实现负责，将同一个 EvidenceStore 和本适配器注入即可；真实音频、设备 SDK 及 HTTP/SSE 未在本次接入。
- asyncio 取消可停止客户端等待、丢弃迟到回答；不保证远端 Ollama 已立即中止 GPU 计算。停止机器人仍由 Runtime 直接处理，不等待模型。
- 工具错误与格式错误明确失败，不自动重试动作。ClassroomHost 按输入编号去重；失败输入禁止重放并取消任务，成功输入返回缓存结果。
- 对话历史仅保留于单次有界决策中；跨轮事实来自教学、任务和观察状态。模型输出仍需真实场景验收，不能把提示词视为消除幻觉的保证。

## 课堂宿主联调补充

`app.classroom_demo` 已连接服务器 Qwen 完成真实知识工具查询和追问讲解；脚本学生随后答完两题，教学状态与任务状态均为 completed，全程未创建动作。设备与感知仍为模拟，学生回答由脚本提供。宿主新增测试覆盖动作成功续接、失败终止、停止、输入去重、连续动作上限及教学完成不掩盖未结束动作；当前 55 项测试通过，Ruff、格式检查及 basedpyright（零错误、零警告）均通过。
