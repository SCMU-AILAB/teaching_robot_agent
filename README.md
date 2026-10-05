# Teaching Robot Agent

## 本地模型与 LangChain

已接入 Ollama 视觉适配器、LangChain 七个工具和有界决策循环。默认复用服务器已有的 `qwen3.5:9b`，宇树 VLM 可作为视觉对照。选型依据、实测结果、环境配置及运行方法见 [本地模型接入](docs/LOCAL_MODELS.md)。

视觉联调入口为 `uv run python -m app.model_demo vision 图片路径 问题`；教学工具联调入口为 `uv run python -m app.model_demo agent 教学问题`。运行前设置 `OLLAMA_BASE_URL`。前者是真实模型读取静态图，后者使用模拟设备与感知，不代表真实机器人已接通。

## 核心工具与本地动作事件

`agent/tools.py` 的 `ToolAdapter(task_id, gateway, education)` 按任务绑定七个入口：`observe_scene`、`lookup_knowledge`、`get_teaching_state`、`evaluate_answer`、`submit_action`、`get_action_status`、`cancel_action`。知识查询返回人工资料及来源；已通过 `agent/langchain_tools.py` 注册为模型工具，也可直接通过 Python 调用。`wait_for_action` 供宿主流程等待真实终态，避免模型轮询。

`runtime/events.py` 的 `ActionEventHub` 独占一个 Runtime 的 `next_event()`，将事件广播给多个订阅者。应用应仅装配一个广播器，显式调用 `start()`，通过 `async with hub.subscribe()` 获取订阅，结束时调用 `close()`。订阅队列溢出会明确报错，调用方重新读取状态；这是本地事件桥，不是带游标重放的 HTTP SSE 服务。

通过 TeamGateway 提交相对移动时使感知缓存失效；观察与移动提交交错或尚有未终结移动动作时，结果标记为 stale。直接绕过该入口操作设备的调用方仍需通知感知版本变化。后续真实感知服务还需处理运动结束后的缓存失效；当前模拟服务每次生成新帧，不复用缓存。

## 课堂宿主

`agent/classroom_host.py` 的 `ClassroomHost(gateway, education, agent_factory)` 负责实际课堂协调：先 `start(task_id)`，应用使用唯一消费者循环调用 `process_next()`，接收 `ClassroomReply` 展示或播报。不同任务由工厂创建独立的 Agent，输入串行处理。

- 带 `question_id` 的答案走确定性教学评价；不带编号的追问交给模型，保留原来的教学问题。
- 模型提交动作后，宿主通过 `wait_for_action()` 等待终态，成功才按最新状态继续决策，不轮询模型。每个输入最多续接三个动作。
- 等待模型或动作时，后续输入留在队列；`cancel(task_id)` 绕过消费锁，取消本地模型等待并让 Runtime 确认停止。
- 相同成功输入重试返回原结果；输入编号冲突会拒绝。处理失败或调用方取消时，宿主取消任务，失败输入不得自动重放；保留教学进度供排查。
- 动作失败、超时或取消不会触发模型自动重试，课堂结束并报告实际状态。零动作课堂可凭已完成教学状态结束，不创建占位动作。
- 应用退出时调用 `close()`，并取消应用自己创建的输入消费循环；宿主不拥有排队等待 `next_input()` 的外部协程。

配置 `OLLAMA_BASE_URL` 后运行 `uv run python -m app.classroom_demo`，可查看真实本地模型回答追问、脚本学生完成两题、教学和任务都进入 completed 的过程。设备和感知为模拟实现，演示未注册移动技能。

## 核心教学流程

运行 `uv run python -m app.teaching_demo` 查看统一输入驱动的完整离线示例。

- `education/service.py`：保存教学状态，评价、重试和推进知识点。
- `agent/context.py`：汇总任务、动作、机器人、教学状态、输入和可选观察；保留实际动作状态，过期观察显式标记。
- `agent/teaching_flow.py`：消费 `TeamGateway.next_input()`，区分追问和答案，产生教学输出。

先创建任务，再调用 `TeachingFlow.start(task_id)`。根据返回的 `state.pending_question.question_id` 调用 `submit_text(..., question_id=...)` 或 `submit_transcript(..., question_id=...)`；单个消费者循环调用 `process_next()`。不传问题编号表示追问，首版仅重述当前知识，不提供自由问答。问题编号应在输入产生时绑定，禁止处理时再猜测所属问题。

输出 `TeachingReply.state` 是处理后进度，`context` 是本轮处理前上下文。重复输入不会重复推进；任务取消后拒绝新的处理，同时保留教学进度。单独使用 TeachingFlow 不会结束机器人任务；ClassroomHost 会在教学完成后核实动作状态并终结任务。

这个离线演示采用人工知识和确定性规则，不调用本地 VLM、LangChain 或语音设备；示例不声称来自真实观察。教学状态及输入结果仅存于内存。

## 队友可直接使用的代码接口

- `domain/services.py`：图片、音频、观察、转写、播放与服务错误的数据结构。
- `perception/interfaces.py`：感知负责人实现 `CameraSource`、`VisionProvider`、`PerceptionService`。
- `speech/interfaces.py`：语音负责人实现 `AudioRecorder`、`ASRProvider`、`TTSProvider`、`AudioPlayer`。
- `storage/evidence.py`：可直接使用的内存 `EvidenceStore`，按编号保存与读取媒体，默认总容量 64 MiB。
- `app/team_gateway.py`：核心提供的任务创建、快照、动作提交、任务取消、文字输入、最终语音去重和观察入口。
- `perception/simulated.py`：无需相机和模型的模拟感知，使用真实登记的占位图片，结果明确标记模拟。

运行完整调用示例：

```bash
uv run python -m app.team_demo
```

队友实现接口中的异步方法后，把实例注入核心服务即可，Python Protocol 无需显式继承。视觉适配器实现 `VisionProvider.analyze()`，当前已提供 `providers/ollama_vision.py`，通过 Ollama 调用本地 Qwen 或宇树 VLM。

当前是可调用的 Python 接口；HTTP/SSE 与真实音频尚未实现；教学输入消费器及独立 VLM 调用已实现，ClassroomHost 已串联出题、模型追问、动作终态等待和教学结束。`TeamGateway.create_task()` 只创建记录，不自动启动教学模型。取消任务会取消 Runtime 动作并拒绝迟到观察/输入，但不会声称已取消尚未接入的录音设备或 GPU 推理。本地事件广播已实现，网络事件接口与完整资源取消仍按 HTTP 合同后续实现。

内部观察使用不可变元组和 `frame`、`analysis` 组合，序列化成 HTTP 字段的转换尚未实现；请导入实际 Python 数据模型，不要照文档重复定义模型。语音播放终态使用 `PlaybackState`，不额外定义同结构的 `PlaybackResult`。

教学机器人的最小可运行基础层。当前实现了数据模型、模拟设备、相对移动 Skill、异步动作 Runtime、任务协调器和内存存储，基础动作演示仅使用 Python 3.12+ 标准库；模型接入及完整测试需安装 `uv.lock` 中的依赖。

## 运行

在项目根目录执行：

```bash
python3 main.py
python3 main.py --scenario all
uv run python -m unittest discover -s tests -v
```

也可以用 `uv run main.py --scenario all`。演示包含正常完成、设备失败、执行超时、运行中取消、排队取消和两个动作顺序执行；失败与超时场景是主动注入的预期结果。

## 开发检查

使用 `uv sync --locked` 安装锁定的开发依赖，随后运行：

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run python -m unittest discover -s tests -v
```

Ruff 负责代码检查、导入排序和统一格式；basedpyright 使用 `recommended` 模式，警告也会使检查失败，业务代码与测试均纳入检查。具体开发约定见 `AGENTS.md`。

编码规范的适用范围和目录对应关系见 [编码规范说明](docs/CODING_SPEC.md)。显式构造函数的关键字参数按规范使用前导下划线，例如 `SimulatedAdapter(_mode=..., _time_scale=...)`、`ActionManager(..., _store=..., _cleanup_timeout_s=...)`。日志级别由环境变量 `LOG_LEVEL` 控制；`.env.example` 提供配置示例，程序不自动加载环境文件。

代码与接口合同由项目负责人本人审核。HTTP 接口将在 [接口合同](API_CONTRACT.md) 中先定义、确认后实现，当前没有已批准的网络端点。

团队联调先阅读 [接口合同草案](API_CONTRACT.md) 和 [模块协作合同](docs/MODULE_CONTRACT.md)：前者定义核心提供给展示端的任务、输入、观察、录音、取消和事件接口；后者定义感知、语音模块的异步方法、共享数据、证据服务以及本地 VLM 适配边界。二者当前均待审核，不代表这些接口已运行。

## 模拟范围

所有设备动作都是**模拟相对移动**，没有连接真实硬件，也不代表目标点导航。成功证据含 `simulated: True`。模拟器的 `time_scale` 只压缩执行时间，距离和速度参数仍使用米和米/秒。

## 按什么顺序读

| 文件                          | 职责                                               |
| ----------------------------- | -------------------------------------------------- |
| `domain/models.py`            | 动作请求、动作记录、任务状态、机器人位姿和状态事件 |
| `robot/base.py`               | 设备适配器契约：连接、查询、相对移动、停止         |
| `robot/simulated.py`          | 可配置成功、失败、挂起的模拟设备                   |
| `skills/base.py`              | Skill 的参数校验、前置检查、执行、验证和清理接口   |
| `skills/move_relative.py`     | 一个完整的相对移动 Skill                           |
| `skills/registry.py`          | 按名称注册和查找 Skill                             |
| `storage/memory.py`           | 内存中的动作与任务记录，读写均复制数据             |
| `runtime/action_manager.py`   | 动作排队、状态转换、超时、取消、停止确认和事件     |
| `runtime/task_coordinator.py` | 创建任务、关联动作、明确结束或取消整个任务         |
| `app/demo.py`                 | 依赖装配与六种演示场景                             |

调用路径是：`演示入口 → TaskCoordinator → ActionManager → RobotSkill → RobotAdapter`。

## 状态与执行约定

- `ActionStatus` 表达某个动作是否排队、执行、验证、取消或结束。
- `RobotState` 表达设备的连接、运动状态及位姿，不代替动作状态。
- `TaskStatus` 表达用户任务是否进行或结束。一个动作成功后任务仍保持运行，调用方通过 `complete_task()` 明确结束多步骤任务。

提交动作会立即返回带有整数编号的 `ActionRecord`。Runtime 的单执行器随后依次执行所有动作，完成验证和清理后再保存终态；`wait_for_action()` 等待终态，`next_event()` 接收状态事件。事件队列当前是单消费者模型，取消等待者不会取消设备动作。

正常状态转换为 `queued → running → verifying → succeeded`。取消排队动作直接得到 `cancelled`；取消执行中的动作先进入 `cancelling`，停止确认后才进入 `cancelled`。执行或验证报错得到 `failed`，超过执行期限得到 `timed_out`。

超时从动作开始执行时计时，覆盖前置检查、执行与验证，不含排队等待；清理另有 `cleanup_timeout_s`。如果无法确认停止，动作记为 `failed`，Runtime 阻止新动作并使已排队动作失败。此时应排查设备状态并建立新的 Runtime。关闭 Runtime 会取消未完成动作、等待清理，再断开设备；断开失败会报告异常。

`complete_task()` 默认要求至少一个动作；传入同任务的已完成 TeachingSession 可结束零动作的纯教学任务。所有实际动作必须已结束；全部成功时任务为 `completed`，否则为 `failed`。`cancel_task()` 先取消全部关联动作，再等待终态；停止失败或已有失败、超时动作时任务记为 `failed`，其余情况为 `cancelled`。这些是首版明确的任务策略，后续教学逻辑可以扩展重试和恢复规则。

Runtime 关闭负责结束动作和设备连接，任务的业务终态由调用方通过协调器结束或取消；应用关闭时应先取消尚未结束的任务。取消 `cancel_task()` 的等待者不会中断后台的任务取消流程。事件流没有结束哨兵，应用退出时应取消自己的事件监听协程（演示入口已处理）。

## 扩展一个动作

1. 继承 `RobotSkill`，给出唯一名称和参数校验。
2. 实现前置检查与执行，设备操作通过 `RobotAdapter` 调用。
3. 用设备反馈验证完成，返回 `SkillResult` 和证据。
4. 实现异常、超时与取消后的清理，并确认设备停止。
5. 在装配入口注册到 `SkillRegistry`，通过协调器提交。

相对移动参数为 `distance_m` 和可选的 `speed_m_s`（默认 0.1）。距离绝对值不超过 2 米，速度大于 0 且不超过 0.5 米/秒；拒绝未知字段、布尔值、非数值及 NaN/Infinity。位置使用平面坐标 `x/y`（米）和 `yaw`（弧度）；记录时间是 Unix 秒，超时由 asyncio 的单调时钟处理。

## 当前边界与下一步

这是单进程、单 asyncio 事件循环、单执行器的基础实现。记录与事件保存在内存，进程退出后不保留；没有数据库恢复；多消费者本地广播由 `ActionEventHub` 提供。异步适配器和 Skill 必须响应协程取消，不能在事件循环中执行阻塞 SDK 调用；真实硬件还需要独立验证停止确认与异常处理能力。

已实现教学服务、统一上下文、LangChain 工具及本地 VLM 适配。接下来对接真实摄像头和语音，课堂宿主已通过输入队列和动作终态等待续接决策；HTTP 接口仍按合同审核后实现。
