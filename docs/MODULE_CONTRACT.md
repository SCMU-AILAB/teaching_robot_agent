# 模块协作合同 v0.1（待审核）

实现进度：共享 Python 模型已在 `domain/services.py`；感知与语音 Protocol 已在 `perception/interfaces.py`、`speech/interfaces.py`；核心本地门面位于 `app/team_gateway.py`。这些接口可直接导入，示例 `python -m app.team_demo`。以下 HTTP 映射及完整生命周期仍属于设计目标；实际内部 ObservationResult 使用 frame+analysis 组合及不可变元组，播放终态统一为 PlaybackState。当前 EvidenceStore 为有容量上限、无自动过期的内存实现，容量不足明确拒绝写入。TeamGateway 输入已支持 question_id；离线 TeachingFlow 消费输入并调用教学评价。LangChain 工具和 Ollama 视觉适配已实现，装配及当前边界见 [本地模型接入](LOCAL_MODELS.md)。

审核人：项目负责人本人。本文提供队友编码所需的 Python 接口签名、数据含义和装配规则；属于草案，不代表实现已存在。网络接口以 [API_CONTRACT.md](../API_CONTRACT.md) 为准。

## 赛题主流程更新

默认入口现在是 RobotApplication → RobotHost → EmbodiedAgent，不要求教学会话。感知通过 PerceptionService 注入，ASR 最终转写仍进入 TeamGateway.submit_transcript；课程模式独立选择。HTTP/SSE 实现状态未变。详见 [赛题框架](COMPETITION_ARCHITECTURE.md)。

## 1. 接口归属

| 负责人 | 实现目录 | 核心提供的依赖 | 交付给核心的对象 |
| --- | --- | --- | --- |
| 感知 | `perception/`、`providers/vision/` | 共享数据契约、EvidenceStore、本地模型部署配置 | CameraSource、VisionProvider、PerceptionService |
| 语音与展示 | `speech/`、`providers/asr/`、`providers/tts/`、`frontend/` | 共享契约、EvidenceStore、任务输入通道、Runtime、HTTP 及事件接口 | AudioRecorder、ASRProvider、TTSProvider、AudioPlayer、前端 |
| 核心系统 | `domain/`、`storage/`、`runtime/`、`skills/`、`agent/`、`education/`、`app/` | 注入以上实现 | 数据契约、装配、教学、动作与任务管理、HTTP 和广播器 |

共享模型与接口先由核心定义，队友导入使用；不要各自定义同名但字段不同的模型。设备 SDK 和模型响应停留在适配器内部。实现可以先用普通类，所有依赖通过构造函数注入，构造参数使用下划线前缀。

首版同一后端进程内以异步方法调用，不要求每个模块各起 HTTP 服务。仅本地 VLM 推理进程按其真实协议通信；部署在另一台局域网机器时也通过配置切换地址。

## 2. 核心提供的数据与证据服务

HTTP 字段与语义见 API_CONTRACT 的共享响应结构；内部模型尚待实现。文件路径只允许出现在后端内部。

### EvidenceStore（核心实现）

| 签名 | 说明 |
| --- | --- |
| `async save(content: bytes, media_type: str, captured_at: float) -> EvidenceReference` | 写入服务管理目录，返回资源编号和元数据 |
| `async read(evidence_id: str) -> bytes` | 读取登记过的媒体；不存在或过期抛出明确错误 |
| `get(evidence_id: str) -> EvidenceReference` | 查询元数据副本 |

EvidenceReference：`evidence_id:str,media_type:str,created_at:float,captured_at:float,size_bytes:int`。首版支持 image/jpeg、image/png、audio/wav；不接受调用方指定磁盘保存路径。存储服务负责大小、保留期和引用策略，读取中的媒体不被清理。时间戳保留采集时刻；created_at 为入库时刻。

FrameReference：`frame_id:str,camera_id:str,captured_at:float,width:int,height:int,evidence_id:str,scene_revision:int`。

AudioReference：`audio_id:str,evidence_id:str,started_at:float,ended_at:float,sample_rate_hz:int,channels:int,duration_s:float`。首版录音统一 16 kHz、单声道、16-bit PCM WAV；适配器明确转换，不能仅修改文件后缀。TTS 音频可用不同采样率，但必须提供真实格式和元数据，播放器据此播放。

### 错误与取消

服务层抛出共享的 `ServiceError(code:int,message:str,retryable:bool)`；由核心转为 HTTP 或资源错误，不要求设备模块返回 HTTP 状态。

- 参数非法：ValueError/TypeError，核心映射 40001。
- 设备或推理服务不可用：ServiceError，50002。
- 调用期限到达：TimeoutError，资源状态 timed_out，错误码 50003。
- 响应无法解析：ServiceError，50001，message 提供可读错误。
- 清理后无法确认停止：ServiceError，50004，交给核心阻止后续动作。
- `asyncio.CancelledError` 必须继续抛出，不能伪装为成功或空结果。

日志可以记录 request_id、task_id、耗时和错误分类，不打印密钥、整段音频或完整模型请求。

## 3. 感知负责人实现的接口

### CameraSource

| 签名 | 行为 |
| --- | --- |
| `async connect() -> None` | 初始化相机，失败抛错误 |
| `async capture(scene_revision: int) -> FrameReference` | 获取新帧，通过 EvidenceStore 保存图片 |
| `async close() -> None` | 释放相机，多次关闭安全 |

实际采集时记录 captured_at。同一相机采集串行化；SDK 阻塞调用必须隔离，线程调用被取消不代表底层设备操作已停止，应等待退出或阻止新采集。相机型号、分辨率、设备编号通过配置注入。

### VisionProvider

`async analyze(request: VisionRequest) -> VisionAnalysis`

VisionRequest：`request_id:str,question:str,frame:FrameReference,timeout_s:float`。

VisionAnalysis：`summary:str,objects:list[ObjectObservation],relations:list[RelationObservation],model_id:str,simulated:bool`。对象与关系字段遵循 HTTP 合同；原始模型文本由适配器解析，不泄漏为未知字典。模型不支持可靠对象框时 bbox=null；不支持对象结构时至少提供有依据的 summary，objects/relations 可为空且不能虚构。空列表只能表示未提取对象，并不保证画面没有对象。

模型输出为外部不可信数据，不能成为执行命令。模型输出中的时间、task_id、证据路径不作为事实字段采纳；这些字段由宿主程序根据真实输入填入。

### PerceptionService

| 签名 | 行为 |
| --- | --- |
| `async observe(request: ObservationRequest) -> ObservationResult` | 返回一次观察或明确错误，由核心管理外层资源状态 |
| `invalidate(reason: str) -> int` | 场景版本加一并返回新版本 |
| `async close() -> None` | 取消自身采集和分析，释放相机 |

ObservationRequest：`observation_id:str,task_id:int,question:str,max_age_s:float,timeout_s:float`，范围与 HTTP 合同相同。

ObservationResult：`snapshot:SceneSnapshotResponse,from_cache:bool,stale:bool`。内部可以采用无 Response 后缀的领域模型，但字段一致；核心负责序列化成 HTTP 模型。

缓存键：规范化前后空白后的完整问题、camera_id、model_id、scene_revision。只有键一致、采集年龄未超过 max_age_s 才复用。max_age_s=0 强制采集；stale 仍按实际年龄计算，因此调用方需要按延迟设置合理阈值。timeout_s 从核心接受请求开始计算，向下游传递剩余预算，不在每层重新开始计时。

移动开始、相机视角变化、人工刷新由核心调用 invalidate。分析返回时若版本已改变，不覆盖当前场景；可作为 stale 历史结果返回。并发分析以采集时间和版本排序，晚完成的旧帧不能覆盖新观察。

感知不轮询 Runtime、不自行取消机器人、不调用教学 Agent；核心拿结果决定下一步。

### 感知交付验收

1. 本地图片模式与真实相机模式使用相同接口。
2. 模拟视觉实现无需模型服务即可运行。
3. 真正调用本地 VLM 时报告真实 model_id，模拟结果标记 simulated=true。
4. 验证超时、断开、格式错误、缓存过期、移动期间观察失效和旧结果晚返回。

## 4. 本地宇树 VLM 适配约定

具体模型/仓库、许可证与版本、推理进程启动方式、硬件要求、协议、图片编码方式、并发能力尚待确认。不得仅凭“宇树 VLM”名称认定支持 OpenAI 兼容服务、流式输出、结构化 JSON 或工具调用。

以下是本项目计划使用的配置名称，不是宇树官方参数：

| 配置 | 内容 | 负责方 |
| --- | --- | --- |
| VLM_PROVIDER | 本地适配器标识，待确定 | 核心与感知协商 |
| VLM_BASE_URL | 实际推理地址，使用环境变量 | 核心部署后提供 |
| VLM_MODEL_ID | 已部署模型标识，不能填猜测名称 | 核心 |
| VLM_TIMEOUT_S | 默认 30 秒，可按实测调整 | 核心 |
| VLM_MAX_CONCURRENCY | 首版 1，显存和响应稳定后再调整 | 核心 |
| VLM_API_KEY | 仅服务确实要求认证时配置，不默认需要 | 核心 |

核心还需要提供：一条已验证成功的脱敏请求/响应、图像分辨率和大小限制、服务健康检查方式。感知负责人实现 `VisionProvider.analyze()` 内部的协议转换，其他模块不感知协议。

模型推理取消可能只取消客户端等待，服务端 GPU 仍在工作。适配器应释放或隔离迟到结果，并遵守并发限制；若实际协议支持请求取消再调用对应能力，不声称强制终止推理。运动与播放停止不能等待 VLM 响应。

本轮不会调用任何云模型、创建密钥或启动推理服务。VLM 首先用于图像理解，上层教学 Agent 的文本决策模型是独立配置，不能默认同一个 VLM 具备所需工具调用能力。

## 5. 语音负责人实现的接口

### AudioRecorder

| 签名 | 行为 |
| --- | --- |
| `async start(recording_id: str, max_duration_s: float) -> None` | 打开后端麦克风并开始录音，返回即表示已开始 |
| `async finish(recording_id: str) -> AudioReference` | 停止采集、保存音频并返回；到时自动停止后仍可取结果 |
| `async wait_finished(recording_id: str) -> AudioReference` | 等待主动结束或最长时长到达；核心用于安排识别 |
| `async cancel(recording_id: str) -> None` | 丢弃输入并停止录音；唤醒等待者，抛 CancelledError |
| `async close() -> None` | 关闭全部录音与设备 |

finish 与 wait_finished 取得同一资源，不重复存储。max_duration_s 自动停止由录音服务负责。核心只创建一个后台识别作业，避免按钮与自动结束同时触发两次识别。多个 finish 调用幂等，未知编号报错。

### ASRProvider

`async transcribe(audio: AudioReference, timeout_s: float) -> TranscriptResult`

TranscriptResult：`text:str,language:str|null,is_final:bool`。首版仅支持最终结果，is_final 必须为 true；无语音时 text=""，核心不提交学生输入。

核心负责调用 `submit_input(task_id, text, kind, question_id, source="speech", recording_id=...)` 的内部输入通道。此签名是待实现入口，提交前检查所属任务仍有效、问题编号仍匹配，按 recording_id 去重。语音模块只返回识别结果，不绕过核心直接调用 Agent。

### TTSProvider

`async synthesize(text: str, voice_id: str | None, timeout_s: float) -> AudioReference`

音频完成合成不代表已播放。文本非空，首版每段最多 2000 字符；长文本由核心分段。音色、模型和服务地址由配置注入。取消后迟到音频可以清理保存，但不得自动播放。

### AudioPlayer

| 签名 | 行为 |
| --- | --- |
| `async play(playback_id: str, audio: AudioReference) -> None` | 开始播放后返回，失败抛异常 |
| `async wait_finished(playback_id: str) -> PlaybackResult` | 等到真实播放结束或已确认停止 |
| `async stop(playback_id: str) -> PlaybackResult` | 请求停止并确认，幂等 |
| `async get_state(playback_id: str) -> PlaybackState` | 获取实际播放状态 |
| `async close() -> None` | 停止播放并释放音频设备 |

PlaybackState：`playback_id:str,status:playing|completed|stopped|failed,started_at:float,ended_at:float|null,error:ResourceError|null`。

PlaybackResult 与 PlaybackState 字段相同，但只能为终态。真正播放完是 completed，中途停止是 stopped，不能将停止当作正常播完。

核心 SpeechSkill 负责：合成 → 检查任务仍有效 → 播放 → 等待完成 → 验证 completed；取消时调用 stop 并确认。合成与播放共用动作 timeout_s 预算，清理另有 Runtime 清理期限。playback_id 由核心根据动作唯一生成，不同动作不能互相停止。

首版录音和播放互斥：录音期间不启动播报，播报期间拒绝新录音。SpeechSkill 获取音频输出占用失败时返回明确错误，不取消已有输入。语音模块维护设备锁，Runtime 管理动作顺序。

#### 音频互斥归属补充（实现事实，合同条款待审核）

2026-10-07 核对：P2 已在 `speech/resources.py` 实现 `AudioDeviceLease`。该对象由语音层定义和维护；应用装配层负责针对同一组录音/播放设备创建一个实例，并将同一个实例注入录音器和播放器。适配器负责取得及释放自己的占用，只有拥有者可以释放；忙碌时拒绝新操作，不打断已有操作。TTS 合成本身不占用音频设备。

目前已验证的是模拟适配器在单进程、单 asyncio 事件循环内的互斥。两个适配器各自使用默认实例时仅适用于独立组件测试，不能保证彼此互斥。真实设备、生产装配和核心 SpeechSkill 尚未接入，因此“录音播放互斥已完成”只限于共享实例注入的模拟链路，不代表跨线程、跨进程或真实硬件互斥。

本补充与上述“语音模块维护设备锁”一致，明确了核心装配方的注入责任；未改变 HTTP 接口或 Runtime 事件消费方式。项目负责人仍须审核合同条款，记录实现事实不代表合同已批准。后续修改归属、占用范围或注入方式时，应同时核对 `speech/README.md` 和双向互斥测试。

### 语音交付验收

1. 使用本地 WAV 即可验证播放、自然结束和中途停止。
2. ASR/TTS 提供模拟实现；普通测试不依赖网络或付费服务。
3. finish/自动结束竞态不重复识别，迟到转写不重复提交。
4. 取消合成不会随后发声，停止失败不能报告播放结束。
5. 关闭与取消不遗留音频线程或设备占用。

## 6. 展示负责人对接顺序

1. 按 HTTP 合同制作示例响应，先显示任务、教学阶段、观察和动作状态。
2. 接 `GET system` 检测后端实例和可用能力。
3. 接任务创建、文字输入与停止按钮。
4. 接 SSE；按合同恢复快照和去重，HTTP 202 仅表示接受请求。
5. 接录音按钮和媒体 content_url；不把转写再次 POST 为文字输入。
6. 页面不直接请求 VLM、不持有模型凭据、不访问服务器文件路径。

## 7. 核心负责人实现顺序与验收清单

- 审核共享字段和服务签名后，创建实际领域模型与类型接口。
- 实现 EvidenceStore 与服务依赖装配，让两位队友有真实依赖可调用。
- 实现状态快照和事件广播器，保留单个 Runtime 事件消费者。
- 实现用户输入队列、取消令牌和 generation/版本检查，防止已取消任务被迟到结果复活。
- 已扩展 TaskCoordinator 的纯教学完成条件：同任务的已完成 TeachingSession 可作为零动作任务完成依据；实际动作仍须全部结束。ClassroomHost 负责传入教学完成状态，不创建假动作。
- 实现独立 SpeechSkill；现有 RobotSkill 的统一“机器人停止”清理假设不直接适用于音频。扩展资源声明/Skill 清理契约，保持移动停止保障。
- 按已审核 HTTP 合同添加路由、统一错误及序列化，再与前端联调。
- 每次提交通过 Ruff、basedpyright 与相关测试。接口变更先更新合同再由项目负责人确认。

变更记录：2026-10-01，首版草案，待项目负责人审核。当时模型细节尚未确定。2026-10-04 已通过现有 Ollama 服务接入本地 VLM，Python 适配与 HTTP 服务实现状态应分别判断。
