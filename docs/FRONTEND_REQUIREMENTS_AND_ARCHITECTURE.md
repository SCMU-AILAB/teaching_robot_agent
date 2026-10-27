# 前端需求审计与页面架构规划（机器人主链路控制台）

审核人：项目负责人（用户本人）。本文件只做审计、分析与规划，不代表任何实现已存在。

用户已确认的四项决策，优先级高于本报告其他建议：

1. 前端范围为机器人主链路控制台（不是课堂模式）。
2. 数据源按 `API_CONTRACT.md` 草案形状做本地 mock，并预留网络适配接口。
3. 需要录音 UI。
4. 预览阶段不修改任何现有文件（包括 `app.dart`、`main.dart`、`api_paths.dart`、`pubspec.yaml`），通过独立入口运行；接线改动留待后续阶段。

## 1. 项目现状

### 1.1 实际应用入口

- 主入口是终端控制台：`main.py` → `app/robot_console.py` 的 `run_console()`，命令为 `/record`、`/done`、`/finish`、`/stop`、`/quit`，支持 macOS/Linux 终端。
- 装配根：`RobotApplication`（`app/robot_application.py:25`），显式注入 `RobotAdapter`、`PerceptionService`、`BaseChatModel`、`KnowledgeService`，可选 `AudioAdapters` 与共享 `EvidenceStore`。
- 没有 HTTP 服务、没有 SSE、没有任何可供浏览器调用的网络端点。`pyproject.toml` 依赖中不存在 Web 框架（仅 langchain、opencv、pillow、pydantic）。

### 1.2 核心模块及职责

| 模块 | 职责 | 关键文件 |
| --- | --- | --- |
| 输入门面 | 任务、统一输入队列、输入去重、观察登记与过期、证据释放 | `app/team_gateway.py` |
| 任务宿主 | 串行决策、动作终态等待、强制重新观察、停止、验收 | `agent/robot_host.py` |
| 决策器 | 有界工具循环（最多 4 轮模型调用）、模型参数校验、4 轮对话记忆 | `agent/embodied_agent.py`、`agent/dialogue.py` |
| 工具 | observe_scene / lookup_knowledge / submit_action 等，按任务绑定 | `agent/tools.py`、`agent/scene_brief.py` |
| 动作运行时 | 动作生命周期、事件单消费者 | `runtime/action_manager.py`、`runtime/events.py` |
| 任务协调 | 任务-动作关联、取消资源屏障、终态回调 | `runtime/task_coordinator.py`、`runtime/task_resources.py` |
| 技能 | move_relative / turn_relative（按能力注册）、speak | `skills/robot_registry.py`、`skills/speak.py` |
| 设备边界 | RobotAdapter 契约与工厂（默认仅 simulated，未知设备拒绝启动） | `robot/base.py`、`robot/factory.py`、`robot/simulated.py` |
| 感知 | CameraSource / VisionProvider / PerceptionService 契约与模拟实现 | `perception/interfaces.py`、`perception/simulated.py` |
| 知识 | 7 条家庭安全示例 + 形状课程条目，带来源标注 | `education/knowledge_service.py` |
| 语音 | 录音→识别→入队；合成→播放→验证；设备租约互斥 | `speech/input.py`、`speech/output.py`、`speech/resources.py` |
| 证据 | 内存 EvidenceStore，64 MiB 上限，任务终态释放 | `storage/evidence.py` |

### 1.3 当前前后端接入情况

**不存在任何前后端通道。** `API_CONTRACT.md` 是 v0.1 草案，文件自述"尚未批准或实现 HTTP 服务"。前端能消费的唯一接口形态就是这份合同草案的响应结构。

### 1.4 现有 Flutter Web 工程情况

- 位置：`frontend/`，Flutter 3.38.2 / Dart 3.10.0，最低 Dart 3.10。
- 现状：单页面 `features/classroom/`（可选课堂模拟），进程内 `MockClassroomService` 驱动，无网络；`lib/core/constants/api_paths.dart` 为空占位并注明"P3 只使用本地模拟数据"。
- 既有约定（新功能必须沿用）：`abstract interface class` 服务边界 + Mock 实现；`ChangeNotifier` 控制器；页面快照带 `instanceId`+`sequence` 去重；迟到快照不覆盖新事件；重连须快照恢复成功才显示在线；widgets 只做展示；页面销毁关闭订阅与定时器；模拟身份在页面常显标注。
- 依赖仅 `flutter` SDK 与 `cupertino_icons`；`assets/scene.png`（项目自绘示例图）已在 pubspec 声明，新功能可直接引用而不改 pubspec。

### 1.5 真实设备与模拟设备的使用情况

- 决策模型：真实本地 Qwen（`providers/local_model.py`，`OLLAMA_BASE_URL` 必填，默认 `qwen3.5:9b`）。
- 机器人：主控制台固定 `SimulatedAdapter`（`robot/simulated.py`），能力为 LOCALIZATION + MOVE_RELATIVE + TURN_RELATIVE，无导航。
- 感知：主控制台固定 `SimulatedPerception`（`perception/simulated.py`），不返回物体；真实链路（LivePerception + OllamaVisionProvider）已实现但未接入主控制台。
- 语音：`ROBOT_AUDIO=off|simulated|real`；real 使用本机麦克风、进程内常驻 Whisper、系统合成（`app/audio.py:65`）。

**结论：当前不存在"真实机器人状态"，一切设备数据均为模拟，UI 必须常显模拟标记。**

## 2. 文档与代码差异

| 差异 | 说明 |
| --- | --- |
| 合同全部端点未实现 | `API_CONTRACT.md` 自述待审核；代码中无任何 HTTP 路由或 Web 框架。不得按合同直接开发网络层。 |
| 合同缺少任务验收端点 | 代码已有验收链路 `RobotHost.finish`（`agent/robot_host.py:127`）→ `TeamGateway.finish_interaction`（`app/team_gateway.py:133`）→ `TaskCoordinator.complete_task(interaction_completed=True)`（`runtime/task_coordinator.py:115`）。合同无对应 HTTP 端点，需补充并审核。 |
| 合同 POST /tasks 含 topic_id 教学语境 | 主链路任务规则明确"不要调用 EducationService.start_session"（`docs/COMPETITION_ARCHITECTURE.md` 任务规则 2）；课堂是独立可选模式。合同该字段与主链路语义冲突，需在审核时澄清。 |
| 合同 ObservationResponse.status 字段 | 内部 `ObservationResult`（`domain/services.py:84`）没有 status 字段，只有 from_cache/stale；合同的 queued/running/succeeded 等是 HTTP 层新增设计，映射时需要核心补状态跟踪。 |
| 合同观察 max_age_s 默认 2 秒 | 核心观察有效窗口是 30 秒（`TeamGateway._observation_ttl_s`），`max_age_s` 只控制感知缓存复用。两者语义不同，前端不能拿 max_age_s 当过期判断。 |
| MODULE_CONTRACT 称语音输入入口"待实现" | 实际 `TeamGateway.submit_text`（`app/team_gateway.py:218`）与 `submit_transcript`（`app/team_gateway.py:233`）已实现并入队去重。文档滞后于代码。 |
| README/架构文档中的测试数 | 均为历史快照（67→282 不等），以 `AGENTS.md` 2026-10-14 记录（282 项 unittest、前端 18 项 widget test）为最新，引用时注明日期。 |
| 前端 README 与代码一致 | 无差异：无网络、无真实设备、模拟标注纪律明确。 |

## 3. 功能审计矩阵

| 功能 | 文档依据 | 代码依据 | 状态 | 输入 | 输出 | 前端可用性 | 数据来源 | 交互需求 | 约束与风险 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 任务创建 | 合同 POST /tasks | `RobotApplication.create_task` → `TaskCoordinator.create_task` | 已实现（无 HTTP） | user_target 非空 | TaskState(task_id,status) | 需要补充后端接口 | 内存 | 创建任务、显示任务状态 | 单活动任务；忙碌期拒绝新请求 |
| 文字输入 | 合同 POST inputs | `TeamGateway.submit_text` | 已实现（无 HTTP） | text 1–4000 字 | UserInput 入队 | 需要补充后端接口 | 内存队列 1024 | 输入框、提交、忙碌拒绝提示 | 失败输入禁止重放；编号去重 |
| 模型问答 | 架构文档主链路 | `EmbodiedAgent.decide` + LangChain 工具 | 已实现 | 队列输入 | 最终文本回答 | 需要补充后端接口 | 真实本地 Qwen | 消息流、回答展示 | 模型文字不自动完成任务 |
| 任务验收 | 无（合同缺口） | `RobotHost.finish` → `complete_task` | 已实现（无 HTTP，合同缺端点） | task_id | TaskState completed/failed | 需要补充后端接口+合同条款 | 内存 | 验收按钮、验收结果 | 处理中拒绝；动作未全终态拒绝 |
| 任务停止 | 合同 PATCH /tasks | `TaskCoordinator.cancel_task` | 已实现（无 HTTP） | task_id | cancelling→cancelled/failed | 需要补充后端接口 | 内存 | 停止按钮、cancelling 中间态 | 资源确认后才终态；停止失败记 failed 并阻止运动 |
| 场景观察 | 合同 POST observations | `ToolAdapter.observe_scene` → `PerceptionService.observe` | 已实现（主控制台为模拟） | question、max_age_s、timeout_s | ObservationResult(summary,objects,stale) | 需要补充后端接口 | 主链路模拟；真实 VLM 已实现未接入 | 观察面板、刷新、stale/模拟标记 | 运动使观察立即失效；30 秒窗口；无 bbox/距离 |
| 物体+方位+安全资料 | MODULE_CONTRACT §3 | `agent/scene_brief.py` build_scene_brief | 已实现 | 观察结果 | 摘要、最多 8 物体（标签+方位）、最多 3 条关联资料、位姿 | 需要补充后端接口 | 知识库 7 条示例 | 物体列表、资料卡片、来源标注 | 资料待审核；无条目时显示"资料库没有相关条目" |
| 知识查询 | README | `KnowledgeService.lookup/for_objects` | 已实现 | query | KnowledgePoint 元组（标题/讲解/来源） | 需要补充后端接口 | 静态知识库 | 经对话回答呈现，不单列页面 | 内容待负责人审核 |
| 机器人动作 | 合同 ActionResponse | `skills/robot_registry.py`、move/turn skill | 已实现（模拟） | skill_name、args、timeout_s | ActionRecord 全生命周期状态 | 需要补充后端接口 | 模拟设备 | 动作列表、状态、失败原因 | 能力门控；失败不自动重试；每请求最多 3 个 |
| 动作事件 | 合同 SSE action.updated | `ActionEventHub.subscribe`（进程内有界订阅） | 已实现（仅进程内） | — | ActionEvent(action_id,status) | 需要补充后端 SSE | 内存 | 动作状态实时刷新 | 订阅溢出需重新取快照；单消费者规则 |
| 对话记忆 | README | `agent/dialogue.py` DialogueMemory | 已实现 | — | 最近 4 轮、当前焦点 | 经对话呈现 | 内存，随任务丢弃 | 追问可用 | 不跨任务；移动后焦点标"移动前" |
| 语音输入 | 合同 POST/PATCH recordings | `SpeechInputService`、`app/audio.py` | 已实现（real/simulated） | /record /done、最长 15 秒 | TranscriptResult → UserInput | 需要补充后端接口 | 本机麦克风（real）或模拟 | 录音按钮、计时、识别中、空语音/失败提示 | 合同明确浏览器不上传音频；录音与播报互斥 |
| 语音输出 | README 核心语音闭环 | `SpeakSkill`、`SpeechOutputService` | 已实现 | 最终回答（≤4000 字，30 秒预算） | 播放终态 | 需要补充后端接口 | 系统 TTS（real）或模拟 | 播报状态、失败保留文字 | 播完才成功；停止≠播完 |
| 证据媒体 | 合同 evidence 端点 | `storage/evidence.py` EvidenceStore | 已实现（无 HTTP） | evidence_id | bytes/元数据 | 需要补充后端接口 | 内存 64 MiB | 观察帧展示（可选） | 任务终态释放；无自动过期 |
| HTTP API | API_CONTRACT.md | 无 | 未实现（草案待审核） | — | — | 暂不可接入 | — | — | 需先审核合同再实现 |
| SSE 事件流 | 合同 GET events | 无（ActionEventHub 可作基础） | 未实现 | — | — | 暂不可接入 | — | — | 需快照+游标原子衔接 |
| 课堂模式 | 架构文档 | `agent/classroom_host.py`、`app/classroom_demo.py` | 已实现（独立可选） | — | — | 不属于本次范围 | — | 现有 Flutter classroom 页面保留不动 | 与主链路不共享输入消费者 |

## 4. 核心用户流程

以代码为准的主链路一次完整交互（对应 `main.py` 控制台语义）：

1. 用户输入任务目标 → 创建任务（running），宿主登记策略，返回"任务已就绪"。
2. 用户提问（文字，或 /record 录音 → /done → 识别 → 转写入队）→ 统一输入队列 → 唯一消费者处理。
3. 模型决策：查询知识 / 调用 observe_scene 观察 / 提交动作。动作进入 Runtime 执行，宿主等待真实终态。
4. 动作成功后：旧观察失效，若任务已有场景观察，宿主强制以 2 秒新鲜度重新观察，再交模型续接；每条输入最多 3 个连续动作。
5. 模型给出最终文字回答 → 启用语音时自动以 speak 动作播报（播完才成功，失败保留文字）→ 返回文本与任务状态。
6. 用户 /finish 验收（全部动作须成功终态，资源空闲）→ completed；或 /stop 停止 → cancelling →（资源确认后）cancelled；停止确认失败 → failed 且 Runtime 阻止后续运动。
7. 异常：动作失败/超时/取消 → 任务取消并记 failed；模型或工具异常 → 宿主先取消任务再传播。

任务内可继续追问（4 轮记忆）；验收或取消后再输入开新任务，记忆不跨任务。

## 5. 前端页面架构

### 5.1 导航结构

```text
应用入口（预览阶段：独立入口 robot_console_main.dart；正式接线后由 app.dart 路由）
└── 机器人主链路控制台（单工作台页面）
    ├── 顶部状态栏：连接状态、机器人状态（模拟标记）、服务能力
    ├── 任务面板：创建任务、任务状态、验收、停止
    ├── 对话面板：消息流、文字输入、录音控制
    ├── 观察面板：场景摘要、物体与方位、关联安全资料、stale/模拟标记
    └── 动作面板：动作记录、状态流转、失败原因
```

### 5.2 为什么是单工作台

- 主链路是**单一任务循环**：同一时刻只有一个活动任务，观察、对话、动作、录音全部围绕该任务上下文展开（`RobotHost` 串行处理，忙碌拒绝新请求）。
- 观察、知识、动作都不是独立业务流程，而是同一对话回合的工具结果——拆成独立路由反而割裂上下文。
- 课堂模式已有独立 Flutter 页面且与主链路互斥（不共享输入消费者），本次不动。
- 不需要首页/设置页：系统无用户体系、无可配置项（配置全在环境变量）。

### 5.3 各区域职责

| 区域 | 业务理由 | 操作 | 展示数据 | 后端依据 | 状态处理 |
| --- | --- | --- | --- | --- | --- |
| 顶部状态栏 | 操作者必须随时知道连接与设备真实性 | 模拟断线/重连（演示） | 连接状态、is_connected/is_moving、位姿（有定位能力时）、simulated 标记、服务状态 | 合同 GET /system；`RobotState`（`domain/models.py:53`） | 断连置灰并提示；重连须快照成功 |
| 任务面板 | 任务是一切的容器 | 创建任务、/finish 验收、/stop 停止 | 目标、状态（pending/running/cancelling/completed/failed/cancelled）、Runtime 阻止原因 | `TaskState`；验收/停止链路 | 忙碌禁用创建；cancelling 中间态；验收失败原因 |
| 对话面板 | 主交互方式 | 文字输入、录音（/record /done 语义） | 用户/机器人消息、录音计时、识别中、播报状态 | `UserInput`、`RobotReply`、recordings 合同 | 忙碌拒绝；空语音、识别失败、合成失败提示 |
| 观察面板 | 物体讲解与安全提醒的依据 | 刷新观察 | 摘要、物体（标签+方位）、关联安全资料（带来源）、stale、simulated、采集时间 | `SceneBrief`（`agent/scene_brief.py:56`） | stale 显著标记；无物体/无资料明确文案 |
| 动作面板 | 动作接受≠完成，必须可见执行证据 | 无（首版不做单动作取消按钮，停止走任务级） | 技能名、参数摘要、queued→…→终态、失败原因 | `ActionRecord`（`domain/models.py:81`） | 终态着色；timed_out/failed 原因展示 |

### 5.4 页面与后端能力映射

所有区域数据源统一走 `RobotConsoleService` 接口（预览阶段由 `MockRobotConsoleService` 实现，模型字段与合同响应结构一致）；正式阶段由 `HttpRobotConsoleService` 实现同一接口，路径集中维护在 `frontend/lib/core/constants/api_paths.dart`（项目规范要求）。页面 Widget 不直接发网络请求。

### 5.5 布局方向（视觉细节属 P2 阶段）

桌面宽屏三栏（对话中栏为主，观察与动作分列两侧），窄屏上下堆叠；任务面板横贯顶部。状态颜色语义：运行中=主色、成功=绿、失败=红、cancelling/verifying=警示色；模拟标记常显。

## 6. 前后端对接方案

### 6.1 分阶段策略

**预览阶段（当前，P1 之后立即执行）：**
- 纯本地 mock，按合同响应结构建模（TaskResponse/ActionResponse/ObservationResponse/SceneSnapshotResponse/RecordingResponse/AssistantMessage 字段名对齐）。
- `RobotConsoleService` 抽象接口即"预留接口"：`events`、`connections`、`snapshot()`、`createTask`、`submitText`、`finishTask`、`stopTask`、`startRecording`、`finishRecording`、`refreshObservation`、`reconnect`、`disconnect`、`dispose`。
- 不改任何现有文件；独立入口 `lib/robot_console_main.dart`；不新增依赖。
- 页面常显"本地模拟"标注，不伪装后端。

**联调阶段（需后端补充后）：**
- 核心负责人按审核后的合同实现 HTTP 路由与 SSE（Web 框架选型属后端决策；`ActionEventHub` 可作事件广播基础，注意其单消费者与有界订阅语义）。
- 前端新增 `HttpRobotConsoleService implements RobotConsoleService`，mock 保留供测试与离线演示。
- 路径集中写入 `api_paths.dart`；服务地址由配置注入，不写进源码。

### 6.2 需要补充的后端能力

1. HTTP 服务本身（路由、统一响应包装、client_request_id 幂等）。
2. **任务验收端点**（合同缺口，须先更新 API_CONTRACT.md 并经负责人确认）。
3. GET /system（映射 `RobotApplication` 状态：机器人、能力、服务就绪、simulated、blocked_reason）。
4. SSE 事件流（快照游标原子衔接、心跳、stream.reset；事件源可基于 `ActionEventHub` + 任务/观察/录音/输入状态扩散）。
5. 证据 content 端点（若观察面板要显示真实帧）。

### 6.3 任务状态与事件处理

- 前端先 GET 任务快照拿 `event_cursor`，再以 cursor 建 SSE；每事件带 `instance_id+sequence`，客户端去重，迟到快照不覆盖新事件。
- stream.reset 或连接断开 → 显示断连、保留最后状态 → 重连且快照恢复成功后才显示在线（沿用现有 classroom 纪律，已是仓库验证过的模式）。

### 6.4 语音对接

- 合同明确：首版录音在后端，浏览器端录音上传不在范围。录音 UI 只发开始/结束/取消语义，展示计时与识别状态；不做浏览器麦克风采集。
- 播报状态经 speak 动作记录呈现（speech_action_id），前端不做音频播放（首版）。

### 6.5 错误与断连

- 统一响应 code 映射用户可读文案；202 已接受后的失败经事件/状态呈现。
- 模拟/真实区分：`simulated` 字段 + 页面常显标注；证据/观察带 `simulated` 与 `stale` 标记。

## 7. 首版功能范围

- **P0（阻塞前端真实联调）：** 后端无 HTTP/SSE；API_CONTRACT.md 未审核且缺验收端点、缺主链路语义澄清；无 system 端点实现。
- **P1（首版必须，预览阶段交付）：** 主链路控制台单页 UI + 合同形状 mock：创建任务、文字输入、录音 UI、观察面板（物体/方位/安全资料）、动作记录、停止（cancelling 中间态）、验收、断线/重连演示、模拟标注。全部新增文件，独立入口，flutter analyze/test/build web 通过。
- **P2（真实现有能力，后续迭代）：** 接线 app.dart 路由；HttpRobotConsoleService 替换 mock；证据帧图片展示；演示场景菜单（沉默/识别失败/合成失败/请求失败）；单动作取消按钮。
- **暂缓（无代码/合同依据）：** 实时视频流、地图与导航可视化、摇杆/远程驾驶、多任务并行、课程管理并入主控制台、题库/学习进度/学生档案、浏览器录音上传、机器人电量/传感器仪表。

## 8. 后续开发建议

1. 负责人审核 API_CONTRACT.md：补验收端点、澄清主链路相对课堂的字段语义（topic_id）。
2. 预览阶段实现 P1 mock UI（本报告第 5 章结构；新文件、独立入口）。
3. 预览通过且负责人批准后：接线 app.dart/入口切换，补 pubspec 依赖（如 http）。
4. 后端按合同实现 HTTP/SSE（建议顺序：system → tasks → inputs → observations → recordings → events → evidence）。
5. 前端实现 HttpRobotConsoleService 并联调：断线重连、运行中取消、识别空文本、播放停止失败、模型超时五类场景必须实测。
6. 真机验收按 `docs/LOCAL_MODELS.md` 末节清单另行安排，不属于前端范围。

## 9. 待确认事项

1. **验收端点是否写入合同**：影响 `RobotConsoleService.finishTask` 的 HTTP 映射；未定前 mock 按"预留"处理。
2. **合同审核与 HTTP 实现排期**：决定 P2 何时可启动。
3. **预览阶段 mock 的语音建模**：用户已要求录音 UI，拟按"启用语音"（ROBOT_AUDIO=real/simulated 语义）建模——回答经 speak 动作播报；是否需要同时提供 off 模式（无播报）切换？
4. **观察帧展示形式**：预览阶段是否复用已声明的 `assets/scene.png` 作为模拟帧占位，还是观察面板首版纯文本？（复用不需改 pubspec。）
5. **独立入口命名与后续接线方式**：`lib/robot_console_main.dart` 是否可接受；接线阶段是替换 app.dart 首页还是加路由。
6. **动作面板信息粒度**：首版展示技能名+参数摘要+状态+失败原因即可；模拟位姿文本（x/y/yaw）是否需要在状态栏展示？
