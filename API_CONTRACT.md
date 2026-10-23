# 接口文档 v1.0

状态：**生效**。2026-10-23 项目负责人指示：仓库对外接口统一规范并最小化，以本文为唯一依据，前端按本文实现。本文取代 v0.1 草案与 `feature/speech-output-lifecycle` 分支上的 v0.2 草案；两者与本文冲突处一律以本文为准。

修改流程：任何接口增删改先改本文并经项目负责人确认，再改代码。前端路径常量统一维护在 `frontend/lib/core/constants/api_paths.dart`。

## 0. 仓库接口总览

| 接口 | 调用方 → 提供方 | 规范位置 |
| --- | --- | --- |
| HTTP API（`/api/v1`） | 前端 → 机器人后端 | 本文第 1–4 节 |
| 底盘串口协议 | 机器人后端 → STM32 底盘固件 | 本文第 5.1 节 |
| 外部推理服务 | 机器人后端 → Ollama、GPT-SoVITS | 本文第 5.2 节 |
| Python 内部模块接口 | 核心 ↔ 感知、语音、设备适配器 | [模块协作合同](docs/MODULE_CONTRACT.md)（只约束进程内调用，不对外） |
| 运行配置 | 部署者 → 各进程 | `.env.example`（环境变量是唯一配置入口，不读取 `.env` 文件） |

### 部署形态

| 机器 | 运行内容 |
| --- | --- |
| Jetson Orin NX（设备端） | 机器人后端主进程与本文 HTTP API；摄像头、麦克风、扬声器、底盘串口；SenseVoice 常驻识别 |
| 3080 服务器 | Ollama（决策模型 `AGENT_MODEL`、视觉模型 `VLM_MODEL`）、GPT-SoVITS 合成 |
| 前端（Mac 桌面端或浏览器） | 只配置 Jetson 的 base URL，不直接访问服务器 |

## 1. 通用规则

- 基础路径 `/api/v1`。base URL 由运行时配置提供，前端不得硬编码主机地址。
- 首版部署于受控局域网，不做身份认证；不得把 HTTP API 暴露到公网。
- 编码 UTF-8；JSON 字段 snake_case。`task_id`、`action_id` 为正整数，其余编号为服务生成的不透明字符串。
- 时间为 Unix 秒（浮点数），时长为秒，坐标为米，朝向为弧度；拒绝 NaN 与 Infinity。
- 可空字段显式返回 `null`，空列表返回 `[]`；请求中出现未定义字段返回 40001。
- 统一响应：`{"code":0,"message":"success","data":...}`；失败为 `{"code":<错误码>,"message":<中文说明>,"data":null}`，HTTP 状态码与错误码对应。二进制与 SSE 响应为仅有的例外，见各接口说明；它们的失败仍返回统一 JSON。
- 创建类 POST 必须携带 `client_request_id`（非空，最多 128 字符）。同一进程内按“路径 + 编号”去重：请求体相同则返回原结果，不同则返回 40009。网络重试必须沿用原编号，避免同一句话被执行两次。
- 首版同时只有一个活动任务；资源只存于内存。每次后端启动生成新的 `instance_id`，前端发现变化后丢弃旧状态，不重放旧命令。
- 停止不经过模型：取消任务直接作用于执行层，接受取消不代表设备已停，停止结果以任务终态为准。

### 错误码

| code | HTTP | 含义 |
| --- | --- | --- |
| 40001 | 422 | 字段缺失、类型、范围或格式不合法 |
| 40009 | 409 | `client_request_id` 已用于内容不同的请求 |
| 40010 | 409 | 当前状态不允许该操作（已有活动任务、任务已结束、设备占用、上一条消息仍在处理） |
| 40401 | 404 | 任务、录音或证据不存在（含已清理的证据） |
| 50001 | 500 | 未分类内部错误；不返回堆栈、路径或凭据 |
| 50002 | 503 | 摄像头、麦克风、扬声器或模型服务不可用或未配置 |
| 50004 | 503 | 设备停止状态无法确认，执行层已锁定，需人工处理后重启 |

异步过程中的失败（识别失败、动作失败）写入对应资源的状态并经事件推送，不再追加 HTTP 错误。

## 2. 数据结构

### SystemResponse

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| instance_id | string | 进程标识，每次启动重新生成 |
| active_task_id | integer/null | 未结束的任务 |
| simulated | boolean | 底盘或感知是否为模拟实现 |
| runtime_blocked_reason | string/null | 非空时禁止一切新动作（对应 50004） |
| robot | object | `connected:bool`、`moving:bool`、`x:float`、`y:float`、`yaw:float`、`updated_at:float` |
| camera | object | `available:bool`、`last_frame_at:float/null`、`error:string/null`；`available=false` 时 `error` 必须非空 |
| audio | object | `available:bool`、`recording:bool`、`playing:bool` |
| services | object | `agent`、`vlm`、`asr`、`tts` 各为 `ready`、`unavailable` 或 `not_configured` |

### TaskResponse

`task_id:int`、`status:string`、`user_target:string`、`created_at:float`、`ended_at:float|null`。

status：`pending`、`running`、`cancelling`、`completed`、`failed`、`cancelled`。后三者为终态。取消时停止确认失败记为 `failed`。

### MessageResponse

用户的提问与机器人的回答使用同一结构，按 `created_at` 排序即为对话记录。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| message_id | string | 消息编号 |
| task_id | integer | 所属任务 |
| role | string | `user` 或 `assistant` |
| text | string | 用户原话或机器人回答文本（纯文本） |
| source | string | 用户消息为 `text` 或 `speech`；机器人消息为 `agent` |
| status | string | 用户消息：`accepted`、`processing`、`answered`、`rejected`、`cancelled`（任务停止时未处理完）；机器人消息恒为 `final` |
| reply_to | string/null | 机器人消息对应的用户消息编号 |
| speech_action_id | integer/null | 机器人回答的播报动作；未启用语音时为 null |
| error | ResourceError/null | 用户消息被拒绝或处理失败的原因 |
| created_at | float | 创建时间 |

回答文本出现不代表已播完；播报进度看 `speech_action_id` 对应动作的状态。

### ActionResponse

`action_id:int`、`skill_name:string`、`args:object`、`status:string`、`created_at:float`、`started_at:float|null`、`ended_at:float|null`、`summary:string|null`、`failure:string|null`。

status：`queued`、`running`、`verifying`、`cancelling`、`succeeded`、`failed`、`cancelled`、`timed_out`。只有经过设备验证的完成才是 `succeeded`。语音播报也是动作（`skill_name=speak`），播完才算 `succeeded`。

### ObservationResponse

观察由机器人在对话中自行发起，前端只读取结果。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| observation_id | string | 观察编号 |
| task_id | integer | 所属任务 |
| question | string | 观察时的问题 |
| summary | string | 画面摘要 |
| objects | list | 每项 `label:string`、`position:string`；position 只取 `左侧`、`中间`、`右侧`、`不确定`，基于相机画面，不代表距离或物理坐标 |
| captured_at | float | 采集时间 |
| analyzed_at | float | 推理完成时间 |
| stale | boolean | 采集期间场景已变化（例如机器人移动），不能当作当前现场 |
| simulated | boolean | 来自模拟感知 |
| frame_evidence_id | string/null | 所用画面，经 `GET /evidence/{evidence_id}` 读取；已清理时为 null |

### RecordingResponse

`recording_id:string`、`task_id:int`、`status:string`、`started_at:float`、`ended_at:float|null`、`transcript:string|null`、`message_id:string|null`、`error:ResourceError|null`。

status：`recording`、`transcribing`、`completed`、`failed`、`cancelled`。识别出非空文本时由后端自动生成一条 `source=speech` 的用户消息并填入 `message_id`；没有识别到语音时为 `completed`、`transcript=""`、`message_id=null`。前端不得再次提交转写文本。

### ResourceError

`code:int`、`message:string`。code 取第 1 节错误码。

### TaskSnapshotResponse

页面初始化与断线重连的唯一快照。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| instance_id | string | 进程标识 |
| task | TaskResponse | 任务 |
| messages | list[MessageResponse] | 最近 100 条，按时间升序 |
| actions | list[ActionResponse] | 最近 200 条，按 action_id 升序 |
| latest_observation | ObservationResponse/null | 最近一次观察 |
| active_recording | RecordingResponse/null | 进行中的录音，没有则为 null |
| truncated | object | `messages:bool`、`actions:bool`，超出条数上限时为 true，不得静默丢弃 |
| event_cursor | string | `<instance_id>:<sequence>`，与快照内容原子生成 |

## 3. HTTP 接口

### GET /api/v1/system

用途：页面启动与设备连接检查。只读内部状态，不触发推理，不打开设备。

响应 200：SystemResponse。错误码：50001。

### POST /api/v1/tasks

用途：开始一次对话任务。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| client_request_id | string | 是 | 去重编号 |
| user_target | string | 否 | 任务目标，最多 2000 字符；省略时为“语音或文字对话” |

响应 201：TaskResponse。错误码：40001、40009、40010（已有活动任务）、50004。

### GET /api/v1/tasks/{task_id}

用途：读取任务快照；建立事件流前必须先调用。

响应 200：TaskSnapshotResponse。错误码：40001、40401。

### PATCH /api/v1/tasks/{task_id}

用途：停止任务或确认完成。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| status | string | 是 | `cancelling`（停止）或 `completed`（确认完成） |

- `cancelling`：活动任务返回 202 与 TaskResponse；后端取消模型等待、动作、录音、识别与播报，全部确认后经 `task.updated` 发布终态。已是终态返回 200 与当前记录。HTTP 连接断开不撤销已接受的取消。
- `completed`：只能由用户确认，模型回答不会自动完成任务。有消息正在处理或正在录音时返回 40010。成功返回 200。

错误码：40001、40010、40401。

### POST /api/v1/tasks/{task_id}/messages

用途：发送一句文字。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| client_request_id | string | 是 | 去重编号 |
| text | string | 是 | 非空，最多 2000 字符 |

响应 202：MessageResponse（`role=user`、`status=accepted`）。机器人回答经 `message.updated` 推送。上一条消息仍在处理、或正在录音时返回 40010，前端应先等待或停止任务。

错误码：40001、40009、40010、40401。

### POST /api/v1/tasks/{task_id}/recordings

用途：使用机器人麦克风开始录音。录音与播报互斥。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| client_request_id | string | 是 | 去重编号 |
| max_duration_s | number | 否 | 默认 15，范围 1–60；到时自动结束并识别 |

响应 201：RecordingResponse（`status=recording`）。错误码：40001、40009、40010（已有录音、正在播报或消息处理中）、40401、50002。

### PATCH /api/v1/tasks/{task_id}/recordings/{recording_id}

用途：结束录音并识别，或放弃本次录音。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| status | string | 是 | `transcribing`（结束并识别）或 `cancelled`（放弃，不生成消息） |

响应：开始识别返回 202，其余返回 200；data 为 RecordingResponse。重复同一操作返回当前结果；已结束的录音再请求识别返回 40010。识别期间放弃会丢弃迟到的识别文本。

错误码：40001、40010、40401。

### GET /api/v1/tasks/{task_id}/events

用途：SSE 推送任务变化，见第 4 节。

请求：`Last-Event-ID` 请求头，或不支持自定义请求头时使用查询参数 `after`，取值为快照中的 `event_cursor`；两者同时存在以请求头为准。

响应 200：`text/event-stream`。建流前的错误码：40001、40401。

### GET /api/v1/camera/snapshot

用途：页面实时画面。前端按 2–5 fps 轮询；单次失败保留上一帧并提示断流。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| max_width | integer | 否 | 160–1920，等比缩小；默认原始分辨率 |

响应 200：`image/jpeg`，带 `Cache-Control: no-store`。返回常驻采集管道的最近一帧，不因请求重开设备；首帧未就绪或偏色未收敛时返回 50002。

错误码：40001、50002。

### GET /api/v1/evidence/{evidence_id}

用途：读取观察画面等已登记媒体。

响应 200：二进制内容，`Content-Type` 为登记时的媒体类型。只按编号解析，不接受任何路径参数。

错误码：40001、40401。

## 4. 事件流

单条消息格式：

```text
id: <instance_id>:<sequence>
event: <type>
data: {"code":0,"message":"success","data":{"instance_id":"...","sequence":12,"task_id":1,"occurred_at":1792800000.0,"type":"<type>","payload":{...}}}

```

| type | payload | 触发时机 |
| --- | --- | --- |
| task.updated | TaskResponse | 任务状态变化 |
| message.updated | MessageResponse | 用户消息创建或状态变化；机器人回答生成 |
| action.updated | ActionResponse | 动作状态变化（含 `speak` 播报） |
| observation.created | ObservationResponse | 一次观察完成 |
| recording.updated | RecordingResponse | 录音状态变化 |
| stream.reset | `{reason:string}` | 游标失效、进程重启或订阅者过慢；客户端重新读取快照后再建流 |

- `sequence` 按任务递增；payload 是完整的最新记录，客户端按编号覆盖，忽略不大于已应用 sequence 的事件。
- 后端保留每个任务最近 1000 条事件，重放游标之后的事件并无缝衔接实时推送。
- 每 15 秒发送一次 SSE 注释作为心跳。

## 5. 设备与外部服务接口

### 5.1 底盘串口协议（后端 → STM32）

- 链路：板上 UART2 口，115200 8N1。打开串口前必须把 DTR、RTS 置低（UART1 带一键下载电路，误触会复位或进入引导程序）。
- 帧：`$<命令>,<字段>,...*<CRC>\n`。CRC-8（多项式 0x07，初值 0，不反射，无输出异或），对 `$` 与 `*` 之间的字节计算，两位大写十六进制。字段均为十进制整数。命令帧最长 64 字节，回传帧最长 96 字节。
- 一致性：Rust 固件与 Python 客户端必须通过同一组测试向量 `firmware/car-core/tests/vectors.txt` 与 `encode_vectors.txt`。

| 方向 | 帧 | 说明 |
| --- | --- | --- |
| 后端 → 底盘 | `$V,seq,vx,vy,wz` | 千分比 −1000..1000：vx 正为前进，vy 正为左移，wz 正为逆时针；单轮满量程 1000 = 每秒 4000 编码器计数 |
| 后端 → 底盘 | `$M,seq,motor,duty` | 单电机标定，motor 1–4，duty 为目标转速千分比 |
| 后端 → 底盘 | `$S,seq` | 立即停车并解除故障锁定 |
| 后端 → 底盘 | `$P,seq` | 心跳，只回复应答，不延长运动 |
| 底盘 → 后端 | `$A,seq,code` | code：0 成功、2 越界、3 未知命令、4 字段数量错误、5 故障锁定中 |
| 底盘 → 后端 | `$E,reason` | 1 校验错误、2 超长、3 格式错误 |
| 底盘 → 后端 | `$T,ms,state,e1,e2,e3,e4,d1,d2,d3,d4` | 20 Hz；state 0 停止、1 运动、2 看门狗停车、3 故障；e 为编码器累计计数，d 为占空千分比 |
| 底盘 → 后端 | `$B,version,port` | 开机帧 |

运动安全：只有 `V`、`M` 刷新 300 ms 看门狗，主机须以约 20 Hz 持续发送；超时自动停车。设计背景见 `docs/superpowers/specs/2026-10-21-car-base-phase1-design.md`。

### 5.2 外部推理服务（后端 → 3080 服务器）

| 服务 | 配置 | 约定 |
| --- | --- | --- |
| Ollama | `OLLAMA_BASE_URL`、`AGENT_MODEL`、`VLM_MODEL` | 只填 HTTP 源地址，不含凭据；视觉模型必须输出 `{"summary","objects":[{"label","position"}]}`，非法输出视为失败 |
| GPT-SoVITS api_v2 | `SPEECH_GPTSOVITS_URL` 及 `SPEECH_GPTSOVITS_*` | 只调用 `POST /tts`，返回 WAV；该服务允许读取任意参考音频路径，只能监听本机，由 Jetson 经 SSH 隧道访问，不得直接开放到局域网。迁移到 3080 之前临时运行在 Jetson 本机（`127.0.0.1:9880`） |

原远程 SSH 语音链路（`speech.real_demo`）保留为备用，属于两个进程间的内部协议，见 `docs/REAL_SPEECH.md`，不对前端开放。

## 6. 与旧草案对照（前端迁移）

| 旧接口或事件 | v1.0 |
| --- | --- |
| `GET /tasks/{id}/session`、v0.1 `GET /tasks/{id}` | `GET /tasks/{id}`，返回 TaskSnapshotResponse |
| `POST /tasks/{id}/inputs`（kind、question_id） | `POST /tasks/{id}/messages`，只有 text |
| `input.updated`、`assistant.message` | 合并为 `message.updated` |
| `teaching.updated`、TeachingStateResponse、答题输入 | 删除；课程答题模式本版不对外开放 |
| `playback.updated` | 删除；播报即 `skill_name=speak` 的 `action.updated` |
| `camera.status`、`GET /device` | 并入 `GET /system` |
| `GET /camera/stream`（MJPEG） | 删除；使用 `GET /camera/snapshot` 轮询 |
| `POST/GET /tasks/{id}/observations/...` | 删除；观察由机器人发起，结果经 `observation.created` 与快照返回 |
| `GET/PATCH /tasks/{id}/actions/{action_id}` | 删除；动作经快照与事件读取，停止使用任务取消 |
| `GET /tasks/{id}/recordings/{recording_id}` | 删除；读取快照的 `active_recording` 或 `recording.updated` |
| `GET /evidence/{id}` 元数据 + `/content` | 合并为 `GET /evidence/{id}` 直接返回内容 |
| 错误码 40402、50003 | 删除；已清理证据返回 40401 |

接口数由 19 个减为 10 个，事件类型由 10 种减为 6 种。

## 7. 变更记录

- 2026-10-01：v0.1 草案。
- 2026-10-20：v0.2 草案（设备状态、摄像头预览、会话快照），未批准。
- 2026-10-23：v1.0 生效。按项目负责人指示统一规范并最小化仓库接口，合并 v0.1/v0.2，补充底盘串口协议、外部服务约定与部署形态。HTTP 服务端尚未实现。
