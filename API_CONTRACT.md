# 接口合同 v0.2（待审核）

审核人：项目负责人（用户本人）。状态：接口设计草案，尚未批准或实现 HTTP 服务。

v0.2 变更摘要（2026-10-20）：新增设备独占与采集约束、`DeviceStatusResponse` 与 `SessionSnapshotResponse` 两个结构、`GET /api/v1/device`、`GET /api/v1/camera/snapshot`、`GET /api/v1/camera/stream`、`GET /api/v1/tasks/{task_id}/session` 四个端点，事件表补 `playback.updated` 与 `camera.status` 两类。**v0.1 原有条目一律未改动**。

本文件是前端与后端路径、参数、响应、错误码的唯一真相来源。Python 内部模块交接见 [模块协作合同](docs/MODULE_CONTRACT.md)。先审核本合同，再实现 HTTP 路由；队友现在可以使用本文示例制作模拟服务和页面。

## 1. 责任与首版范围

| 提供方 | 提供给队友的能力 |
| --- | --- |
| 核心系统负责人 | 任务、用户输入、统一状态、动作查询与取消、事件广播、证据读取、服务装配 |
| 感知负责人 | 摄像头、场景观察服务、本地 VLM 适配器、观察缓存 |
| 语音与展示负责人 | 录音、识别、合成、可停止播放、前端页面 |

首版默认摄像头、麦克风和扬声器连接后端所在的机器人/开发机；前端通过 HTTP 控制后端设备。浏览器端录音上传不在首版范围，避免重复采集与设备所有权冲突。首版一次只有一个活动教学任务。

本地 VLM 由核心负责人部署，感知负责人调用。模型名称、协议、输入限制尚未确认，不假设宇树模型支持特定 SDK、OpenAI 兼容接口、工具调用或深度定位。

## 2. 通用规则

- 基础路径 `/api/v1`，服务地址通过配置提供，不写进源码。
- JSON 字段 snake_case；任务和动作编号沿用正整数，其余资源编号为服务生成的不透明字符串。
- 时间均为 Unix 秒（浮点数），时长为秒；坐标米、朝向弧度。数值拒绝 NaN/Infinity。
- 可空字段显式返回 `null`，列表无内容返回 `[]`；拒绝未定义的请求字段。
- 成功：`{"code":0,"message":"success","data":...}`。失败：`{"code":40001,"message":"参数不合法","data":null}`，匹配 HTTP 状态码。
- 日期、标识符及观察内容示例均为模拟数据，不代表真实观测。
- POST 创建请求携带 `client_request_id`，非空字符串，最多 128 字符。同一进程内按请求路径和该编号去重：相同请求返回原资源；不同请求体返回 409/40009。请求重试沿用编号。
- PATCH 取消是幂等操作：终态资源返回当前记录，不重启动作。取消请求被接受不代表设备停止。
- 资源与去重记录首版存于内存；每次后端启动生成新的 `instance_id`。重启后前端清空旧资源，重新建立任务，不自动重放旧控制命令。
- 本地单用户演示按受控网络部署；身份认证、多租户与公网访问不属于本合同首版。

### 设备独占与采集约束（v0.2 新增，待审核）

- 摄像头、麦克风、扬声器由服务进程**独占持有**。HTTP 端点只读取服务内部状态，不按请求打开或重建设备。
- RealSense 同一路视频流不能被两个进程同时打开。实测第二个采集进程报 `Call to S_FMT failed for YUYV @ 960x540: Device or resource busy`，因此本地 VLM、预览端点与其他消费者**必须复用同一采集管道**，任何模块都不得自行打开 `/dev/video*`。
- 拉起采集管道存在固定启动开销（实测单帧含启动 0.47 秒）。快照端点必须返回常驻管道的最近一帧，**不得每次请求重启管道**。
- 摄像头首帧存在自动曝光与白平衡收敛过程（实测前若干帧整体偏色）。首帧不可用时返回 50002，不得把偏色帧当作正常画面返回。
- 采集管道异常退出后，服务应自动重建并更新 `camera.last_frame_at`；重建失败持续返回 50002。

### 通用错误表

| code | HTTP | 含义 |
| --- | --- | --- |
| 40001 | 422 | 字段、类型、范围或格式不合法 |
| 40009 | 409 | 幂等编号与已存在的请求内容冲突 |
| 40010 | 409 | 当前状态不允许操作，或已有活动任务/设备占用 |
| 40401 | 404 | 任务、动作、录音或证据不存在 |
| 40402 | 410 | 证据已清理 |
| 50001 | 500 | 未分类内部错误，响应不返回堆栈或凭据 |
| 50002 | 503 | 摄像头、语音或模型服务不可用/尚未配置 |
| 50003 | 504 | 同步设备查询超时 |
| 50004 | 503 | 设备停止状态不明，Runtime 已阻止后续动作 |

创建长任务已返回 202 后，后续失败写入资源状态和事件，不再尝试返回第二次 HTTP 错误。框架参数校验错误也包装为统一响应。

## 3. 共享响应结构

### TaskResponse

沿用现有 TaskState：`task_id:int`、`user_target:str`、`status:str`、`action_ids:list[int]`、`created_at:float`、`ended_at:float|null`。

status：`pending/running/cancelling/completed/failed/cancelled`。等待学生输入时任务仍为 running，由教学状态表示等待。任务拥有者被取消后，禁止该任务的新决策、新动作和迟到播报。

### ActionResponse

沿用 ActionRecord：`action_id:int`、`raw_request:{task_id,skill_name,args,timeout_s}`、`status:str`、`created_at:float`、`started_at:float|null`、`ended_at:float|null`、`result:{summary:str,evidence:object}|null`、`failure:str|null`。

status：`queued/running/verifying/cancelling/succeeded/failed/cancelled/timed_out`。提交成功只说明入队；succeeded 必须有完成证据。移动及播报使用同一 Runtime，首版串行执行。

### TeachingStateResponse

`task_id:int`、`topic_id:str|null`、`goal:str`、`stage:str`、`pending_question:{question_id:str,text:str}|null`、`last_feedback:str|null`。

stage：`not_started/explaining/awaiting_answer/feedback/completed`。这是新增设计，尚未实现教学模块。问题编号仅对所属任务有效。

### ResourceError

`code:int`、`message:str`、`retryable:bool`。用于录音、输入与观察的异步失败。代码采用上方分类；取消不作为错误。ActionResponse 保留现有 failure 文本字段，由事件报告失败状态。

### EvidenceResponse

`evidence_id:str`、`media_type:str`、`created_at:float`、`size_bytes:int`、`content_url:str`。

content_url 为 `/api/v1/evidence/{evidence_id}/content`。前端不接收服务器绝对文件路径；输入也不允许指定任意本地文件路径。首版进程内资源过期策略由核心存储服务统一管理。

### SceneSnapshotResponse

`scene_id:str`、`task_id:int`、`question:str`、`captured_at:float`、`analyzed_at:float`、`scene_revision:int`、`summary:str`、`objects:list[ObjectObservation]`、`relations:list[RelationObservation]`、`evidence_ids:list[str]`、`simulated:bool`、`model_id:str`。

ObjectObservation：`object_id:str`、`label:str`、`attributes:dict[str,str]`、`bbox:[float,float,float,float]|null`。

bbox 为 `[x_min,y_min,x_max,y_max]`，归一化到 0..1，左上角原点；最小值不得大于最大值。对象编号只在该 scene_id 内有效。

RelationObservation：`subject_id:str`、`relation:str`、`object_id:str`；两端必须引用当前场景对象。左右方向基于相机画面，RGB 结果不得冒充物理位置或距离。不强制模型提供置信度。

### ObservationResponse

`observation_id:str`、`task_id:int`、`status:str`、`created_at:float`、`ended_at:float|null`、`snapshot:SceneSnapshotResponse|null`、`from_cache:bool`、`stale:bool`、`error:ResourceError|null`。

status：`queued/running/succeeded/failed/cancelled/timed_out`。stale 在读取时按采集时间和场景版本计算；即使推理完成，图片过旧也只能作为 stale 证据展示，Agent 必须刷新或明确告知无法获取新观察。失败不伪装成空对象列表。

### DeviceStatusResponse（v0.2 新增，待审核）

`instance_id:str`、`uptime_s:float`、`camera:CameraStatus`、`audio:AudioStatus`。

CameraStatus：`available:bool`、`device:str`、`width:int`、`height:int`、`fps:float`、`last_frame_at:float|null`、`error:ResourceError|null`。

device 为设备名称而非序号（序号随拔插漂移，见模块协作合同同名约定）。`available=false` 时 `error` 必须非空，不得返回空对象。

AudioStatus：`input_device:str`、`output_device:str`、`recording:bool`、`playing:bool`、`volume_percent:int|null`。

volume_percent 指播放设备的当前音量百分比；设备无音量控件时返回 null。前端据此解释“已播但无声”的设备问题，不得自行假定音量正常。

### SessionSnapshotResponse（v0.2 新增，待审核）

`instance_id:str`、`task:TaskResponse`、`teaching:TeachingStateResponse|null`、`actions:list[ActionResponse]`、`latest_observation:ObservationResponse|null`、`recording:RecordingResponse|null`、`playback:PlaybackState|null`、`assistant_messages:list[AssistantMessage]`、`truncated:{actions:bool,messages:bool}`、`event_cursor:str`。

PlaybackState：`playback_id:str`、`status:str`、`started_at:float`、`ended_at:float|null`、`error:str|null`。status：`playing/completed/stopped/failed`。

这是页面初始化与断线重连的**唯一聚合快照**：一次请求拿到任务、教学阶段、动作、最近观察、当前录音、当前播放与消息列表，避免前端串联十余个接口。`event_cursor` 与 `GET /api/v1/tasks/{task_id}/events` 的 `Last-Event-ID` 同格式，客户端拿到后直接建流，不重复拉取。

`actions` 按 `action_id` 升序，首版最多返回最近 200 条；`assistant_messages` 最多返回最近 100 条，两者都超出时在同一响应中用 `truncated:{actions:bool,messages:bool}` 标明截断，不得静默丢弃。

## 4. HTTP 接口

以下每项均为待审核草案。成功响应表均描述统一响应中的 data。

### GET /api/v1/system

描述：前端初始化，获取进程标识、服务能力和当前任务；不触发模型推理。

请求参数：无。

响应（200）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| instance_id | string | 进程标识 |
| active_task_id | integer/null | 当前未终结任务 |
| robot | object | RobotState：is_connected、is_moving、position{x,y,yaw}、updated_at |
| simulated | boolean | 设备是否模拟 |
| runtime_blocked_reason | string/null | 无法安全执行的原因 |
| services | object | perception/asr/tts 各为 ready/unavailable/not_configured |
| vlm | object | model_id:string/null、status:ready/unavailable/not_configured |

错误码：50001、50003。组件不可用尽量在 services 中表达。

变更记录：2026-10-01，初始草案，待项目负责人审核。

### POST /api/v1/tasks

描述：接受用户目标，创建任务并安排首轮决策；不在请求中等待推理或动作完成。

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| client_request_id | string | 是 | 请求去重编号 |
| user_target | string | 是 | 非空目标，最多 2000 字符 |
| topic_id | string/null | 否 | 指定教学主题；省略为 null，由核心服务选择 |

响应（202）：TaskResponse。

错误码：40001、40009、40010、50002、50004。预检关键依赖不满足时不启动任务。

变更记录：2026-10-01，初始草案，待审核。

### GET /api/v1/tasks/{task_id}

描述：获取任务和教学快照，前端重连后重建状态。

请求参数：路径 task_id，正整数，必填。

响应（200）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| task | TaskResponse | 任务状态 |
| teaching | TeachingStateResponse | 教学进度 |
| actions | list[ActionResponse] | 所有关联动作 |
| inputs | list[InputResponse] | 已提交输入及处理结果 |
| observations | list[ObservationResponse] | 已提交观察请求及结果 |
| recordings | list[RecordingResponse] | 当前任务录音及转写状态 |
| messages | list[AssistantMessage] | 已生成的教学文本，供重连恢复 |
| event_cursor | string | `<instance_id>:<sequence>`，与整份快照在同一版本原子生成 |

AssistantMessage：`message_id:str,text:str,created_at:float,speech_action_id:int|null`。文本生成、音频播放、动作完成独立显示；已有文本不代表已播完。

错误码：40001、40401。

变更记录：2026-10-01，初始草案，待审核。

### PATCH /api/v1/tasks/{task_id}

描述：前端停止按钮使用的任务级取消；绕过模型决策，直接请求执行层停止。

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| status | string | 是 | 仅允许 cancelling |

响应：活动任务返回 202 和 TaskResponse（cancelling）；已有终态返回 200 和当前 TaskResponse。

接受取消后：禁止新动作；取消关联动作、观察、录音/识别/合成工作，阻止迟到结果触发后续行为；确认资源停止后发布任务终态。运动停止确认失败时 task=failed，并阻止新运动。保持现有失败/超时动作导致任务取消最终为 failed 的策略。HTTP 连接断开不撤销已接受的取消。

错误码：40001、40401、50001。停止失败通过后续任务状态与 runtime_blocked_reason 呈现。

变更记录：2026-10-01，初始草案，待审核。

### POST /api/v1/tasks/{task_id}/inputs

描述：提交文字提问或答案；语音最终转写也通过核心的相同输入通道处理。

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| client_request_id | string | 是 | 去重编号 |
| kind | string | 是 | question 或 answer |
| text | string | 是 | 非空，最多 4000 字符 |
| question_id | string/null | 条件必填 | answer 必须匹配当前待答问题；question 为 null |

响应（202）：InputResponse：`input_id:str,task_id:int,kind:str,text:str,question_id:str|null,source:text|speech,recording_id:str|null,status:accepted|processing|processed|rejected|cancelled,created_at:float,error:ResourceError|null`。

同一任务输入按接受顺序处理；答案在实际处理时再次校验问题编号，已过期答案变为 rejected。提交普通追问首版不会自动取消正在移动的动作；停止走任务取消接口。

错误码：40001、40009、40010、40401。

变更记录：2026-10-01，初始草案，待审核。

### GET /api/v1/tasks/{task_id}/actions/{action_id}

描述：查询动作真实进度和完成证据。

请求参数：task_id、action_id，正整数，必填；动作必须属于该任务。

响应（200）：ActionResponse。

错误码：40001、40401。

变更记录：2026-10-01，初始草案，待审核。

### PATCH /api/v1/tasks/{task_id}/actions/{action_id}

描述：取消一个动作，不自动结束教学任务；全局停止使用任务级取消。

请求体：status:string，必填，仅 cancelling。

响应：终态或排队动作立即取消返回 200；仍在取消中返回 202；data 均为 ActionResponse。

错误码：40001、40401。

变更记录：2026-10-01，初始草案，待审核。

### POST /api/v1/tasks/{task_id}/observations

描述：请求观察，后台执行摄像头采集与本地 VLM 推理。

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| client_request_id | string | 是 | 去重编号 |
| question | string | 是 | 非空观察问题，最多 1000 字符 |
| max_age_s | number | 否 | 默认 2，范围 0..30；0 强制重新采集，结果仍按实际年龄标记 |
| timeout_s | number | 否 | 默认 30，范围 0 < 值 <= 120，包含采集、排队和推理 |

响应（202）：ObservationResponse。通过 observation.updated 事件或 GET 查询完成结果。

错误码：40001、40009、40010、40401、50002。

变更记录：2026-10-01，初始草案，待审核。

### GET /api/v1/tasks/{task_id}/observations/{observation_id}

描述：查询观察状态、采集时间与证据。

请求参数：task_id 正整数；observation_id 非空字符串，必须属于该任务。

响应（200）：ObservationResponse。

错误码：40001、40401。

变更记录：2026-10-01，初始草案，待审核。

### POST /api/v1/tasks/{task_id}/recordings

描述：使用后端麦克风开始录音；首版最多一个活动录音。播报期间拒绝录音，返回设备占用。

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| client_request_id | string | 是 | 去重编号 |
| kind | string | 是 | question 或 answer |
| question_id | string/null | 条件必填 | answer 匹配当前待答问题 |
| max_duration_s | number | 否 | 默认 15，范围 1..60 |

响应（201）：RecordingResponse：`recording_id:str,task_id:int,kind:str,question_id:str|null,status:recording|transcribing|completed|failed|cancelled,started_at:float,ended_at:float|null,evidence_id:str|null,transcript:str|null,input_id:str|null,error:ResourceError|null`。

达到最大时长自动结束录音并转写；只有最终非空转写被核心提交一次输入。无语音返回 completed、transcript=""、input_id=null。问题过期/任务取消时不自动提交答案。前端不重复转发最终转写。

错误码：40001、40009、40010、40401、50002。

变更记录：2026-10-01，初始草案，待审核。

### PATCH /api/v1/tasks/{task_id}/recordings/{recording_id}

描述：结束录音并识别，或丢弃本次录音/识别。

请求体：status:string，必填，允许 transcribing（结束并识别）或 cancelled（取消且不提交输入）。

响应：启动识别返回 202，其余返回 200；data 为 RecordingResponse。重复相同操作返回当前结果；已取消或失败后请求转写返回 40010。识别期间取消会阻止迟到文本提交。

错误码：40001、40010、40401。

变更记录：2026-10-01，初始草案，待审核。

### GET /api/v1/tasks/{task_id}/recordings/{recording_id}

描述：查询录音、识别以及关联输入状态。

请求参数：task_id 正整数；recording_id 非空字符串且属于该任务。

响应（200）：RecordingResponse。

错误码：40001、40401。

变更记录：2026-10-01，初始草案，待审核。

### GET /api/v1/evidence/{evidence_id}

描述：获取媒体元数据，不暴露本地绝对路径。

请求参数：evidence_id 非空字符串。

响应（200）：EvidenceResponse。

错误码：40001、40401、40402。

变更记录：2026-10-01，初始草案，待审核。

### GET /api/v1/evidence/{evidence_id}/content

描述：显示图像或播放已有音频。

请求参数：evidence_id 非空字符串。

响应（200）：二进制媒体，Content-Type 与证据元数据一致。这是统一 JSON 响应的媒体例外；失败仍为统一 JSON 错误。服务端只按登记编号解析路径，不接受客户端路径。

错误码：40001、40401、40402。

变更记录：2026-10-01，初始草案，待审核。

### GET /api/v1/device（v0.2 新增，待审核）

描述：查询设备端能力与健康状态，用于页面初始化提示与故障定位；不触发推理、不打开设备。

请求参数：无。

响应（200）：DeviceStatusResponse。

错误码：50001。

变更记录：2026-10-20，设备端补充草案，待审核。

### GET /api/v1/camera/snapshot（v0.2 新增，待审核）

描述：返回摄像头最近一帧 JPEG，用于页面定时刷新画面。不重启采集管道。

请求参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| max_width | integer | 否 | 缩放目标宽度，范围 160..1920；按比例缩放，默认返回原始分辨率 |

响应（200）：`image/jpeg` 二进制，带 `Cache-Control: no-store`。这是统一 JSON 响应的媒体例外；失败仍为统一 JSON 错误。

错误码：40001（max_width 越界）、50002（摄像头不可用或首帧未就绪）。

建议前端按 2–5 fps 轮询，单帧实测约 110 KB、局域网取帧约 32 ms。轮询不得因单次失败清空页面已有画面，应保留最后一帧并显示断流提示。

变更记录：2026-10-20，设备端补充草案，待审核。

### GET /api/v1/camera/stream（v0.2 新增，待审核）

描述：MJPEG 连续流，用于页面实时预览。

请求参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| fps | integer | 否 | 推送帧率，范围 1..15，默认 10；不得超过采集管道帧率 |

响应（200）：`multipart/x-mixed-replace; boundary=frame`，每段包含 `Content-Type: image/jpeg` 与 `Content-Length`。这是统一 JSON 响应的流式例外；建流前失败仍为统一 JSON 错误。

客户端断开后，服务必须停止对该订阅者的推送并释放订阅槽，**不得影响采集管道与其他订阅者**。订阅者处理过慢时丢弃旧帧而不是累积积压。

错误码（建流前）：40001、50002。

前端适配说明（非合同约束）：浏览器可用 `<img src>` 直接显示；Flutter Web 需通过 `HtmlElementView` 承载 `<img>`，`Image.network` 不支持 `multipart/x-mixed-replace`。若适配成本过高，页面可只使用快照轮询。

变更记录：2026-10-20，设备端补充草案，待审核。

### GET /api/v1/tasks/{task_id}/session（v0.2 新增，待审核）

描述：一次取回任务的聚合快照，供页面初始化与断线重连；比逐接口拉取更适合弱网与重连场景。

请求参数：task_id 正整数。

响应（200）：SessionSnapshotResponse。

错误码：40001、40401。

后端必须原子生成快照与 `event_cursor`，避免前端在快照与建流之间漏事件。`recording` 与 `playback` 无进行中资源时为 null，不是空对象。

变更记录：2026-10-20，设备端补充草案，待审核。

### GET /api/v1/tasks/{task_id}/events

描述：SSE 推送任务变化；动作完成触发后端下一轮决策，前端无需反复询问模型。

请求参数：task_id 正整数；可选 Last-Event-ID 请求头或 after 查询参数，格式为 `<instance_id>:<sequence>`。

响应（200）：`text/event-stream`，这是统一 JSON 响应的流式例外。单条消息格式：

```text
id: instance-demo:12
event: action.updated
data: {"code":0,"message":"success","data":{"instance_id":"instance-demo","sequence":12,"task_id":1,"occurred_at":1790812800.0,"type":"action.updated","payload":{"action_id":1,"raw_request":{"task_id":1,"skill_name":"move_relative","args":{"distance_m":0.3,"speed_m_s":0.1},"timeout_s":10.0},"status":"running","created_at":1790812799.0,"started_at":1790812800.0,"ended_at":null,"result":null,"failure":null}}}

```

| type | payload |
| --- | --- |
| task.updated | TaskResponse |
| teaching.updated | TeachingStateResponse |
| action.updated | ActionResponse |
| observation.updated | ObservationResponse |
| recording.updated | RecordingResponse |
| input.updated | InputResponse |
| playback.updated | PlaybackState（v0.2 新增，待审核；合成完成不等于播放完成，只有实际播完才是 completed） |
| camera.status | `{available:bool,device:str,last_frame_at:float|null,error:ResourceError|null}`（v0.2 新增，待审核；状态变化才推送，不按帧推送） |
| assistant.message | AssistantMessage |
| stream.reset | `{reason:string}`，客户端重新 GET 任务快照 |

核心提供广播器，唯一消费现有 Runtime.next_event()，再分发给决策器和所有前端订阅者；前端不得直接竞争读取 Runtime 单消费者队列。每任务 sequence 递增，客户端按 instance_id+task_id+sequence 去重。

每 15 秒发送 SSE 注释心跳。每任务保留最近 1000 条事件（内存）。前端先 GET 任务快照，再用其 event_cursor 作为 Last-Event-ID 建流，重放游标之后的事件；后端必须原子建立快照和游标，并原子衔接重放与实时订阅，避免遗漏变化。无游标、游标失效、进程改变或订阅者过慢时发送 stream.reset 并关闭；客户端重新获取快照和新游标再连接。浏览器客户端若使用不支持自定义请求头的原生 EventSource，可通过可选查询参数 after 传入同格式游标；两者同时存在时以 Last-Event-ID 为准。事件 payload 为可替换快照，客户端忽略已应用或更旧的 sequence。

错误码（建流前）：40001、40401、50001。建流后错误通过流事件或连接关闭处理。

变更记录：2026-10-01，初始草案，待审核。

## 5. 最小联调顺序

1. 核心提供 system、任务快照及事件的模拟响应，展示负责人先画状态页面。
2. 感知负责人按内部服务合同完成摄像头和本地 VLM 适配；核心注册服务并挂接观察资源。
3. 语音负责人按内部接口完成录音/识别和合成/播放；核心转发最终输入并实现 SpeechSkill。
4. 验证运行中取消、模型超时、摄像头失联、识别空文本、播放停止失败及前端重连。

v0.2 建议追加的联调顺序（待审核）：

5. 设备端先提供 `GET /api/v1/device` 与 `GET /api/v1/camera/snapshot`，前端用真实画面替换页面静态图，确认“画面 + 设备状态”可用。
6. 再补 `GET /api/v1/tasks/{task_id}/session`，前端用它替换模拟数据源，页面其余部分不变。
7. 最后接 `GET /api/v1/tasks/{task_id}/events` 与 `GET /api/v1/camera/stream`，处理断线重连、断流保留最后一帧、摄像头失联提示。

部署形态（v0.2 补充，待审核）：首版 HTTP 服务运行在设备端（Jetson），`/api/v1` 与摄像头同源，前端只配置一个 base URL。若后续改为服务器统一网关，本合同的路径与字段不变，仅 base URL 改变；前端不得硬编码主机地址。设备端与服务器之间如何传输画面，属于服务器网关阶段的问题，不在本合同首版范围。

首版不开放前端任意执行 Skill 的端点：教学动作由核心决策器提交。播报走 SpeechSkill，前端展示 speech_action_id 对应状态；无需单独的任意播报 HTTP 入口。

## 6. v0.2 待负责人确认（草案，未批准）

以下五项在本合同批准前不得实现，逐项给出建议只是为了便于决断，不代表已决定。

| # | 待定事项 | 建议 |
| --- | --- | --- |
| 1 | 部署形态：前端直连设备端，还是服务器统一网关 | 首版直连设备端（已验证同源可取画面与状态）；网关阶段再迁移，路径不变 |
| 2 | 摄像头预览：只做快照轮询，还是同时提供 MJPEG | 先快照轮询（前端改造成本最低），MJPEG 作为可选增强 |
| 3 | `PlaybackState.error` 类型：`str\|null` 还是 `ResourceError` | 用 `str\|null`（与当前代码一致），同时修正模块合同里的旧写法，避免两套错误模型并存 |
| 4 | `SessionSnapshotResponse` 是否包含 `assistant_messages` | 建议包含；但需先确认消息存储归属（核心还是语音），否则该字段无法落地 |
| 5 | 新增事件 `playback.updated`、`camera.status` 是否批准 | 建议批准；两者都只在状态变化时推送，不按帧推送，避免放大事件量 |

另需确认：`/api/v1/camera/*` 首版是否保持无鉴权（当前假设受控局域网部署），若比赛现场网络不可信，需要另行设计令牌机制，不在 v0.2 范围。
