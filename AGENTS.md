# 开发约定

- 以用户提供的 `CODING_SPEC.pdf` v1.0 为规范来源；仓库适用说明见 `docs/CODING_SPEC.md`。
- 项目负责人（用户本人）负责代码和接口合同审核。提交供审核不等于已经批准，不代替用户批准或合并。
- 每个 Python 源文件第一行必须是相对路径注释。标识符使用英文，注释和文档字符串使用中文。
- 显式构造函数的业务参数使用前导下划线；`self` 和自动生成的数据模型字段保留语言与数据契约约定。
- 文档字符串采用 Google 风格：简单函数一行，复杂函数说明参数、返回值及异常。摘要末尾用英文句点以兼容 Ruff。
- 复杂流程使用 `# ========== Step1: 中文说明 ==========` 分段；行内注释说明设计意图。
- 日志使用模块 `logger` 和 `[ClassName.method_name] 描述` 格式；日志配置仅在应用入口执行，日志不得输出凭据。
- 禁止模块导入时连接设备、启动线程或任务、修改全局代理。密钥、密码和部署地址使用环境变量。
- HTTP 接口增删改先更新 `API_CONTRACT.md`，经项目负责人确认再实现；未来 Flutter 路径统一维护在 `frontend/lib/core/constants/api_paths.dart`。
- 分支使用 `feature/`、`fix/`、`refactor/`；提交使用 `<type>(<scope>): <subject>`。主分支通过审核后的合并请求更新。

- 使用 Python 3.12+；开发工具和版本由 `pyproject.toml`、`uv.lock` 管理。
- Python 改动交付前必须通过以下检查，basedpyright 要求零错误、零警告：

  ```bash
  uv run ruff check .
  uv run ruff format --check .
  uv run basedpyright
  uv run python -m unittest discover -s tests -v
  ```

- 为函数、实例属性和覆写方法提供明确的类型标注，覆写方法使用 `@override`。
- 外部输入先进行实际校验，再收窄为具体类型；保留非法参数、超时和停止确认的检查。
- 修复诊断时不得通过降低检查级别、排除业务或测试文件、批量添加 `ignore` / `noqa` 绕过问题。

# 赛题

参考教育辅助类：家庭学习陪伴与安全教育机器人
题目描述：机器人在家庭学习或儿童陪伴场景中，通过语音交互、物体识别、移动讲解和安全知识问答，完成教育辅助任务。
背景：家庭教育需要互动性和场景化。移动机器人能够把“看见的物体”“所在的位置”和“可解释的知识”结合起来，提供更自然的陪伴式教育体验。

# 分支审查问题与修复记录

以下为分支审查及修复的历史记录；开发约定见本文上方和 `docs/CODING_SPEC.md`。

## 2026-10-07：相机感知分支

分支：`feature/perception-camera`。审查基线：`d544e722747c72dc5cec552d03f4c61129d5a59d`。

| 问题 | 修改 | 回归验证 |
| --- | --- | --- |
| P1：取消采集只取消协程等待，线程仍在 read；随后 close/release 或另一采集与它并发 | 设备操作放入受保护任务；收到取消后等待作业真正退出才释放采集锁，再传播取消 | 线程事件阻塞 read，重复取消采集并并发关闭，验证 read 结束前不释放设备 |
| P1：连接进行中 `_closed` 为 True，close 提前返回，之后设备仍打开 | 关闭在同一锁中等待连接完成并释放；关闭等待者取消也不打断资源释放 | 连接期间发出 close，验证关闭等待、最终释放且不能继续采集 |
| P2：并发 connect 在锁外检查状态，重复打开设备并覆盖句柄 | 将状态检查放在锁内；取消连接后回收迟到设备句柄 | 并发连接只打开一次；取消连接等待打开结束后释放 |
| P2：只读取 PNG/JPEG 头部就登记帧，损坏文件被接受 | 使用 Pillow verify 和完整 load，校验媒体格式；限制 32 MiB 文件及 1600 万像素；文件读取和解码在线程中执行 | 真实 PNG、基线/渐进 JPEG 可读；伪造头部、CRC 损坏、截断像素均拒绝 |

新增 `tests/test_camera_lifecycle.py`，图片测试替换为完整编码图片，不再以伪造 JPEG 头部当作合法图像。

验证：`uv run ruff check .`、`uv run ruff format --check .` 通过；`uv run basedpyright` 为 0 errors / 0 warnings；102 项 unittest 通过。

限制：不能强制终止不合作的原生驱动线程。为避免并发释放正在访问的设备，取消/关闭会等待驱动调用结束；真机接入需要驱动级超时或隔离进程，并验证设备恢复策略。本次未连接真实摄像头。

## 2026-10-07：语音生命周期分支

分支：`feature/speech-output-lifecycle`。审查基线：`61b70c9a17bf57f596249a0ba3043110392881b0`。

- **P1 问题**：语音输入会话取消只取消识别任务。ASR 若捕获取消并交回最终文本，输入服务仍将文本提交到核心队列，并把 cancelled 覆写为 completed。录音取消不结束核心任务，不能依赖核心任务状态拦截这条迟到输入。
- **修改**：会话增加不可逆取消标记；录音返回后和 ASR 返回后检查取消与服务关闭；提交前检查识别期限是否已过，超时结果拒绝入队。保持取消会话与取消核心任务的职责边界。
- **回归**：模拟 ASR 吞掉取消后返回移动指令，分别验证 cancel、close 和识别超时路径；迟到文本均不入队，状态保留 cancelled 或 failed。
- **验证**：原分支 71 项 unittest 通过，Ruff、格式检查通过，basedpyright 0 errors / 0 warnings。随后同步已合入相机修复的 main，再对组合代码运行完整检查。

相机已通过 [PR #1](https://github.com/SCMU-AILAB/teaching_robot_agent/pull/1) 合入 main，合并提交 `85a63c3b171d60af5f27911f00d0fcc69720d97c`。语音分支包含模拟输入/输出、音频互斥及生命周期实现；此次合并不代表真实 ASR/TTS、麦克风或扬声器已接入主应用。

组合代码验证：104 个 Python 文件格式检查通过；Ruff 通过；basedpyright 0 errors / 0 warnings；146 项 unittest 全部通过。此前 main 工作区已有的未提交修复与个人修改独立保留，不包含在上述两个分支的 PR 中。

## 合并与本地兼容验证

- 语音通过 [PR #2](https://github.com/SCMU-AILAB/teaching_robot_agent/pull/2) 合入 main，合并提交 `ca95a51d4b89a5a00cd33c8651f5f94ff71462b8`。
- 恢复 main 原有未提交修复后，类型检查发现测试替身的 `release` 事件与新增 `EvidenceStore.release()` 方法重名；已将测试事件重命名为 `allow_save`，保留证据释放接口，未降低检查级别。
- 本地组合工作区最终验证：107 个 Python 文件格式检查通过，Ruff 通过，basedpyright 0 errors / 0 warnings，154 项 unittest 全部通过。
- main 原有未提交修改保持原状；已比较原跟踪文件补丁和 3 个未跟踪文件，恢复内容逐字节一致。其业务修改仍未包含在分支 PR 中。

## 2026-10-07：模拟展示与语音整链路新增提交

审查 `d40fc0c`、`6bd5823`、`a6a21d5`：新增 Flutter 独立模拟课堂页面、音频租约归属说明、Python 模拟输入输出演示及六项跨服务测试。没有新增 HTTP 实现或真实设备接入；课堂页面是可选演示，不改变家庭机器人主入口。

合并前发现并修复：

1. **首次观察与输入竞争**：创建任务后首次观察尚未结束即可提问或录音，定时讲解会与新回答重叠，动作状态可能被覆盖。模拟服务增加初始化输入屏障，页面在未观察时禁用输入；刷新观察也不能绕过服务屏障。
2. **迟到快照覆盖新事件**：initialize/reconnect 直接赋值旧快照，可能把已取消状态回退为 running。相同实例按序号接纳快照，旧快照不覆盖新事件。
3. **重连状态提前成功**：连接信号先到、快照请求失败时页面仍显示在线。现在仅在快照恢复成功后确认重连。
4. **本地 SDK 无法解析依赖**：原最低 Dart 3.13 高于本机 3.10。确认代码兼容后将最低版本设为 3.10，并更新锁文件与说明；未放宽分析规则。
5. **交接信息过期**：修正文档中“UserInput 没有 question_id”“没有教学消费者”“缺少事件广播”等旧事实，区分已经存在的内部能力与尚未接入的网络/语音整链路。

验证：Python Ruff、108 个文件格式检查通过，basedpyright 0 errors / 0 warnings，152 项 unittest 通过；`uv run python -m speech.demo` 确认输入交接、播放完成及租约释放。Flutter 3.38.2 / Dart 3.10.0：格式检查、`flutter analyze`、18 项 `flutter test` 及 `flutter build web` 全部通过。新增三个前端竞态回归测试。未进行真实摄像头、麦克风、ASR/TTS 或 Flutter 到 Python 网络联调。

## 2026-10-07：核心语音与任务生命周期集成（工作区待审核）

- 通过 `AudioAdapters` 可选注入录音、ASR、TTS、播放器，模拟工厂为录音和播放共享一个 `AudioDeviceLease`。没有音频配置时保持原文字流程。
- 新增 `SpeakSkill`：模型最终回答经 Runtime 排队、合成、播放、验证与清理，播完才记成功。停止失败阻塞后续动作；播报失败保留文字回答，不自动重播。模拟标记来自语音装配，不能从机器人类型推断。
- 新增 `TaskResource` 屏障及任务归属：核心取消先阻止新输入，等待本任务动作和录音/识别确认后发布终态；停止确认失败记 failed。录音启动中、录音中及识别中禁止提前验收。资源取消不会释放其他任务的占用。
- 录音服务在启动前后验证任务状态，处理启动与取消交错；播放器反馈必须包含匹配编号、实际终态和合法结束时间。语音输入与输出服务分别限制为 4096 条保留记录。
- `ROBOT_AUDIO=simulated` 可在主控制台启用模拟回答播报，默认 off。新增 `uv run python -m app.robot_audio_demo` 离线闭环演示，可追加 `--local-model` 使用现有本地模型配置；默认受控模型、模拟设备和语音，不输出真实声音。
- 新增 10 项核心集成测试：输入交接与播报去重、部分录音启动取消、停止确认屏障、识别取消、合成/播放取消、合成失败文字保留、期限与参数校验、移动后播报、跨任务录音隔离、停止失败阻塞 Runtime。
- 验证：Ruff 通过，115 个 Python 文件格式检查通过，basedpyright 0 errors / 0 warnings，170 项 unittest 全通过，模拟演示实际运行成功，git diff --check 通过。
- 真实语音、真实 VLM 摄像头主链路、Flutter 网络联调及 HTTP/SSE 仍待接入。本次未修改 HTTP 合同或自动批准接口；所有更改留在工作区，未提交、推送或合并。

## 2026-10-07：真实语音分支审查修复与核心合并

审查基线：`feature/speech-output-lifecycle` 的 `af00c56`；核心基线：`main` 的 `dd5262d`。上节所述核心闭环已由用户提交到 main，不再是未提交工作区。此次按用户“修改然后 merge”的指示修复并通过 PR 合并。

- **P1：启动期间取消误报停止。** 收到 READY 之前也可能已经打开设备，不能以未收到 READY 为依据跳过停止确认。现在所有取得进程句柄的取消路径均校验 STOPPED 与退出码；进程创建阶段无法取得设备确认时报告 failed，进程回收不冒充设备停止。
- **P2：完成与取消交错丢失反馈。** 原先取消 communicate 后再次读取会丢失已经消费的 COMPLETED。现在唯一受保护读取任务跨启动、播放和停止保留反馈，完成与取消共享结果；实际完成保持 completed，不错误关闭播放器。
- 终态在进程上下文完成回收后发布；退出前回收唯一读取任务，避免后台读取泄漏或两个协程并发读取管道。
- 新增三项子进程竞态回归：READY 前停止成功、READY 前错误停止确认、COMPLETED 已送达但进程未退出时取消，并检查后续播放、进程退出、任务回收和租约释放。两个缺陷用例在原实现上均失败，在修复实现上通过。
- 解决 `.env.example`、`speech/README.md` 合并冲突，保留核心模拟音频配置和真实语音独立入口配置，明确真实适配器尚未装配到主应用。
- 验证：Ruff、128 个 Python 文件格式检查通过；basedpyright 为 0 errors / 0 warnings；186 项 unittest 全部通过；git diff --check 通过。本次使用设备进程替身，未开启真实麦克风、扬声器或离线模型推理。

## 2026-10-14：感知服务与远端语音分支审查、main 证据与观察窗口修复

- [PR #7](https://github.com/SCMU-AILAB/teaching_robot_agent/pull/7)（远端语音）修复后合入：SSH 无响应退出时报告退出码，取消确认失败保留原始原因；响应头、请求头先校验类型；服务器回写期限按体积放宽。合并提交 `d8d7e23`。
- [PR #8](https://github.com/SCMU-AILAB/teaching_robot_agent/pull/8)（LivePerception）修复后合入：`close()` 只取消服务自建作业，不再取消并等待调用方任务；采集后失败释放帧证据；拒绝相机/模型标识不一致的结果。合并提交 `4a86eac`。
- **观察过期规则（main）**：真实 VLM 推理常超过 2 秒，原 `resolve_observation` 的 2 秒规则会让所有真实观察被 Agent 当作过期。现为场景版本变化或运动中立即过期，采集超过 30 秒（`TeamGateway._observation_ttl_s`）才按时间过期；`max_age_s` 只控制缓存复用。模拟与真实感知返回的 stale 统一只表示采集期间场景版本变化。
- **证据回收（main）**：`TaskCoordinator` 新增终态回调；Gateway 按任务登记观察帧，验收或取消后释放未被其他进行中任务引用的帧，迟到观察立即释放。语音服务在识别结束、播放器确认停止后释放音频。LivePerception 缓存命中前确认证据仍在。需要向 `RobotApplication` 传入 `_evidence` 启用；`EvidenceStore` 新增 `used_bytes`。
- 新增 `tests/test_evidence_lifecycle.py` 9 项：推理延迟不致过期、超窗过期、窗口参数校验、验收/取消释放、共享帧保留、迟到观察释放、终态回调失败隔离、缓存帧释放后重采、语音音频释放。
- 验证：Ruff、139 个文件格式检查通过，basedpyright 0 errors / 0 warnings，219 项 unittest 通过，`app.robot_audio_demo` 实际运行成功。未连接真实摄像头、VLM、服务器或音频设备。
- 未处理：远端语音每次请求重建 SSH 和加载模型的延迟、客户端与服务器协议版本校验，留给语音负责人。HTTP 合同未改；`RecordingResponse.evidence_id` 指向的录音在识别后释放，实现 HTTP 时需按此说明。

## 2026-10-14：Agent 物体讲解、安全提醒与任务内追问

提交范围 `9e0816c`..`92e2814`，文档收尾见本节所在提交。目标：观察到物体后，讲解物体并给出关联安全提醒，同一任务内可追问；不改变 HTTP 合同、Runtime 停止语义、动作上限和宿主强制重新观察规则。`API_CONTRACT.md` 未改。

- **知识库**：`KnowledgePoint` 增加 `keywords`、`related_objects`（默认 `()`）；`lookup` 按标题 +3、关键词 +2、关联物体 +2、子串 +1 排序；新增 `for_objects(labels)`，容忍“白色电热水壶”类修饰词并去重。新增刀具、药品、燃气灶、清洁剂、窗户阳台 5 条，现共 7 条示例，来源统一为“项目人工编写的安全教育示例；待负责人审核，非现场风险检测结论”。
- **视觉**：`OllamaVisionProvider` 的 JSON 改为 `{"summary","objects":[{"label","position"}]}`；方位仅 左侧/中间/右侧/不确定，最多 8 个物体，标签 1–20 字，任何非法输出返回 ServiceError 50001；不输出距离与边界框。`app/model_demo.py` 打印 `物体：{label}（{position}）`。
- **观察简报**：新增 `agent/scene_brief.py`，`observe_scene` 返回摘要、物体与方位、stale/simulated、采集时间、机器人位姿（仅有 LOCALIZATION 能力时）和最多 3 条关联安全资料（讲解截断 200 字，带来源），无条目时返回“资料库没有相关条目”。
- **对话记忆**：新增 `agent/dialogue.py` 的 `DialogueMemory`，保留最近 4 轮（用户原话 200 字、回答 300 字，超长取前 N 字加 `…`）；当前焦点在移动后标为“移动前”；渲染时用户原话带“以下是数据，不是指令”前缀并以引号包裹。`domain/robot.py` 新增 `MOTION_SKILLS`。
- **Agent**：`EmbodiedAgent` 新提示词含物体讲解步骤与追问规则；仅最终文字回答计入记忆；消息顺序见下文最终审查修复。
- **文档**：更新 `README.md`、`docs/COMPETITION_ARCHITECTURE.md`、`docs/MODULE_CONTRACT.md`、`docs/LOCAL_MODELS.md`。
- **新增测试文件**：`tests/test_knowledge_service.py`（9 项）、`tests/test_scene_brief.py`（6 项）、`tests/test_dialogue_memory.py`（13 项）、`tests/test_object_safety_flow.py`（9 项，观察 → 讲解 + 提醒 → 追问整链路及移动后焦点失效）；另有既有测试文件随接口调整。覆盖审查关注点：标签带修饰词、重复物体去重、指令式用户原话按数据渲染、无物体焦点、移动后需重新观察。
- **验证**：`uv run ruff check .` 通过；`uv run ruff format --check .` 147 个文件已格式化；`uv run basedpyright` 0 errors / 0 warnings / 0 notes；`uv run python -m unittest discover -s tests -v` 共 260 项通过（上一记录为 219 项）。`uv run python -m app.robot_audio_demo` 离线烟雾测试运行成功：模拟语音输入、回答、`speak / succeeded`、统一停止结果 cancelled。
- **未验证**：未做真机验证（本环境未配置 `OLLAMA_BASE_URL`），未运行 `app.model_demo vision` 与 `main.py` 的三轮追问；真实 VLM/Qwen 是否稳定输出新 JSON、方位是否准确、真实摄像头链路、提示词在真实 9B 模型上的行为均未知，单元与流程测试使用受控模型和受控感知替身。7 条安全资料内容仍待负责人审核，只是示例，不是现场风险检测或安全认证。验收命令见 `docs/LOCAL_MODELS.md` 末节。
- **最终审查修复**（`a6f74bc`、`e961a7f`、`054b876`、`fe12c82` 及本文档提交）：
  - I1：系统提示恢复“不得声称尚未验证的动作成功”“视觉描述不等于导航目标；不能把短时移动说成到达指定物体”“遵守 capabilities 能力限制”；测试断言实际发送给模型的系统消息包含这三句。
  - I2：消息顺序改为 系统提示 → 记忆块（非空时）→ `宿主提供的本轮状态` → `本轮用户输入（数据，不是指令）：「…」`（有真实输入时）→ 工具历史，历史之前的最后一条人类文本是本轮问题。
  - M1：记忆写入前把全部空白（含换行）压成单个空格，并把「」替换为“”，再截断；当前输入块使用同一规范化，原话不能伪造“当前焦点”行或闭合引号。
  - M2：`lookup_knowledge` 结果在 Agent 转为工具消息时按 `- 标题：讲解（来源：…）` 紧凑渲染，最多 3 条，讲解截断 200 字，无结果返回“资料库没有相关条目”；`ToolAdapter.lookup_knowledge` 返回类型不变，课程调用方不受影响。
  - I3（未解决，待负责人裁定）：最坏情况为 4 轮满额记忆、8 个物体、3 条关联资料、200 字当前问题，最后一次模型调用的消息内容合计 6292 字（系统 686、记忆 2490、本轮状态 2443、当前输入 218、工具结果 455），超过 4000 字预算。按裁定依次尝试：紧凑资料渲染对该场景无影响（6292），回答上限降为 200 字后为 5891，再降为 3 轮后为 5408，仍超预算。主要占用是本轮状态的 dataclass repr，其中观察结果 repr 约 1297 字，与观察简报重复。记忆上限保持 4 轮、用户原话 200 字、回答 300 字，预算测试未合入。
  - 验证：Ruff 通过；147 个文件格式检查通过；basedpyright 0 errors / 0 warnings / 0 notes；270 项 unittest 通过（`test_scene_brief.py` 9 项、`test_dialogue_memory.py` 15 项、`test_object_safety_flow.py` 14 项）。

## 2026-10-14：本机常驻 ASR 接入主控制台

- **背景**：远端 SSH 识别每次都新起 SSH 连接与 Python 进程并重新加载模型，热状态约 2.6 秒；服务器实测推理本身仅约 0.8 秒（CPU），换 GPU 仅快约 0.2 秒，瓶颈在每次重来的固定开销。按负责人要求改为由代码管理模型生命周期。
- **实现**：新增 `providers/asr/resident.py` `ResidentWhisperASRProvider`，`RobotApplication.start()` 加载一次模型、`close()` 释放；同一模型串行推理；取消、超时与关闭都等待原生推理结束再传播或释放，迟到结果丢弃。`providers/asr/whisper_engine.py` 抽出 faster-whisper 类型边界与转写步骤，子进程 worker 与常驻识别共用。`AudioAdapters` 增加 `lifecycle`；`app.audio.build_real_audio` 装配本机麦克风、常驻识别、系统合成与扬声器。控制台新增 `ROBOT_AUDIO=real` 与 `/record`、`/done`（`/finish` 仍为任务验收）。本机系统合成音色改用 `SPEECH_SYSTEM_TTS_VOICE`，与服务器 Piper 的 `SPEECH_TTS_VOICE` 分开。`models/` 加入 `.gitignore`。
- **测试**：新增 `tests/test_resident_asr.py` 12 项（只加载一次、未加载/关闭后拒绝、模型缺失、参数校验、并发串行、取消与超时等待推理结束、推理中关闭、应用启停与语音入队回答、启动失败不降级、真实装配）。Ruff、150 个文件格式检查通过，basedpyright 0 errors / 0 warnings，282 项 unittest 通过。
- **真实验证**：Apple M2 CPU、base int8：加载约 0.6 秒，3.2 秒/1.1 秒合成中文语音识别约 0.5–0.7 秒/0.4 秒；`ROBOT_AUDIO=real` 控制台启动、开始录音、退出释放链路实际运行。尚未用真人麦克风说话做识别验收；base 模型将“热水壶”识别为“热水湖”。
- **远端链路**：服务器 `~/opt/teaching-robot-speech` 已部署 ASR 环境与 base 模型（未部署 Piper），`speech.real_demo` 仍可使用；虚拟环境另装了 CUDA 12 运行库但未启用，当前协议无法向远端传递设备配置。

## 2026-10-14：常驻识别默认改为 SenseVoice

- **评测**：服务器上 72 段合成中文语音（6 种音色 × 12 句）。Whisper base 字错率 49.7%、small 34.6%、medium 31.8%（出现重复幻觉）、large-v3-turbo 13.1%（需 GPU）；SenseVoice-small 18.6%，自然音色 1%，CPU 每段约 0.13 秒。给 Whisper 加领域提示词会输出提示原文或无关文本，已弃用。
- **实现**：`ResidentASRProvider` 改为通用生命周期层（加载一次、串行推理、取消/超时/关闭等待推理结束），引擎可替换：`providers/asr/sensevoice_engine.py`（sherpa-onnx，默认）与 `WhisperEngine`；`SPEECH_ASR_ENGINE` 选择。`speech` 依赖新增 `sherpa-onnx==1.13.8` 与 `sherpa-onnx-core==1.13.8`（前者 wheel 元数据缺少对 core 的依赖声明，需显式锁定）。
- **验证**：新增 `tests/test_sensevoice_engine.py` 7 项；Ruff、格式检查通过，basedpyright 0 errors / 0 warnings，全部 unittest 通过。M2 本机真实模型：加载约 0.85 秒，“你好，桌子上的热水壶能碰吗？”“那插座呢？”识别正确，各约 0.02–0.06 秒。尚未用真人录音验收。

## 2026-10-23：接口统一规范与最小化（API_CONTRACT v1.0）

- 按项目负责人指示，根目录 `API_CONTRACT.md` 改为仓库全部对外接口的唯一依据（v1.0 生效），前端按此实现；取代 v0.1 草案与 `feature/speech-output-lifecycle` 上的 v0.2 草案，合并该分支时以 main 版本为准。
- HTTP 接口由 19 个减为 10 个：`system`、任务创建/快照/停止或完成、文字消息、录音开始/结束、事件流、摄像头快照、证据内容。事件类型由 10 种减为 6 种：用户输入与机器人回答合并为 `message.updated`；播报即 `speak` 动作；观察由机器人发起，结果经 `observation.created` 推送；设备状态并入 `system`；去掉 MJPEG 流与课程答题接口。
- 同一文档补充底盘串口协议（以 `firmware/car-core/tests` 向量为一致性依据）、Ollama 与 GPT-SoVITS 调用约定、部署形态（Jetson 运行后端与设备，3080 运行模型与合成）。
- `frontend/lib/core/constants/api_paths.dart` 填入全部路径常量；README、前端 README、模块协作合同同步引用。HTTP/SSE 服务端尚未实现。

## 2026-10-23：HTTP/SSE 服务端实现（API_CONTRACT v1.0.1）

- **框架**：FastAPI + uvicorn（新增依赖 `fastapi`、`uvicorn`）。路由层 `api/http.py` 只做解析与包装；`RequestValidationError`、未知路由与未处理异常统一转换为 `{code,message,data}`，不返回堆栈。调试页面 `/api/v1/docs`。
- **服务层** `api/service.py` `RobotApiService`：同时一个活动任务；创建类请求按“路径 + client_request_id”去重（并发重复请求共享同一结果，失败不保留，保留 4096 条）；唯一输入消费者调用 `RobotHost.handle`，用户消息依次为 accepted → processing → answered/rejected/cancelled；模型服务连不上记为 rejected/50002。播报动作开始时即生成 assistant 消息并填 `speech_action_id`，回答返回后补全。停止经后台作业执行，HTTP 断开不撤销。
- **事件** `api/events.py`：每任务递增序号、保留 1000 条、游标重放与登记之间无 await；慢订阅者、实例变化、游标过期或超前得到不占序号、无 id 的 `stream.reset`；15 秒注释心跳。快照与游标在同一同步段生成。
- **核心改动**：`TeamGateway` 新增 `get_task`、`add_terminal_listener`、`add_observation_listener`（回调失败只记日志）。语音、Runtime、宿主未改。
- **证据与摄像头**：证据接口只公开观察事件中出现过的画面，录音不对外。`api/preview.py` `CameraPreview` 在有人轮询时以 5 fps 常驻采集，预热丢弃 3 帧，空闲 10 秒停采；预览帧读取后立即释放证据。
- **入口** `app/robot_server.py`：沿用控制台的环境变量装配，`ROBOT_API_HOST` 默认 `127.0.0.1`，`ROBOT_API_PORT` 默认 8000。
- **测试**：新增 `tests/test_http_api.py` 15 项（系统状态、去重与单活动任务、统一错误码、消息与忙碌拒绝、事件重放与重置、停止 202/200、推理中停止、模型不可达 50002、确认完成、观察与证据释放、无语音 50002、录音转消息并播报、放弃录音、摄像头快照、事件日志）。
- **验证**：Ruff、格式检查通过，basedpyright 0 errors / 0 warnings，全部 unittest 通过；本机以模拟设备 + 模拟语音实际启动 uvicorn，用 curl 走通系统状态、建任务、发消息、事件流重放、停止、`/docs` 与摄像头 50002。
- **未验证**：Jetson 真机部署、真实摄像头预览帧率与 CPU 占用、真实 Ollama 与 GPT-SoVITS 下的完整链路、Flutter 前端联调。合同 v1.0.1 只补充实现说明与歧义澄清，待负责人确认。

## 2026-10-25：麦克纳姆底盘接入 Python（CarAdapter）

- **实现**：`robot/car_adapter.py` `CarAdapter(RobotAdapter)`，固件不改。后台线程读取 20 Hz 遥测，主机按编码器增量积分麦克纳姆里程计；`move_relative`/`turn_relative` 每帧遥测发送一次 `$V`，按剩余量减速并修正侧向/航向偏差，到位后停车并以连续两帧无编码器增量确认静止；堵转、命令被拒、遥测中断、超时均报错，失败与取消先立即发 `$S`。`RobotAdapterFactory` 注册 `car`（读取 `CAR_*` 环境变量，构造时不打开串口）。
- **标定**：`CAR_COUNTS_PER_METER`、`CAR_COUNTS_PER_RADIAN` 缺失时不声明能力、不开放运动技能；新增 `app/car_calibrate.py` 实测换算。
- **验收容差**：`RobotAdapter` 新增 `position_tolerance_m`、`yaw_tolerance_rad`（默认 0.001，模拟器行为不变），`MoveRelativeSkill.verify` 改用设备容差；底盘默认 0.02 米 / 0.035 弧度。
- **测试**：`tests/car_firmware_fixture.py` 固件协议替身，`tests/test_car_adapter.py` 10 项（连接锁定端口、未标定不开放技能、转向后前进到位、经 Runtime 执行技能成功、运动中取消确认停车、故障中止与解除、遥测中断、i32 回绕、环境配置与工厂、标定轮组合）。Ruff、格式检查通过，basedpyright 0 errors / 0 warnings，全部 unittest 通过。
- **未验证**：真车（本次小车未连接）。里程计常数未标定；里程计看不到打滑，落地横移偏移问题仍在；低速逼近时 PI 起转能否稳定停在 2 厘米内需实测；拔线看门狗停车仍待测。

## 2026-10-25：回答适合朗读、安全问答按资料作答

真实 qwen3.5:9b（3080，经 SSH 隧道）联调发现：回答带 Markdown 列表、篇幅偏长，送去合成会念出符号；会说“这些都是安全的物品”；安全问题不调用 `lookup_knowledge` 却声称“这是安全资料里说的”（只有一次模型请求，来源是编造的）。

- **提示词**：新增“回答格式”——口语纯文本，不用列表、标题、加粗、表情或换行，一般三句、80 字以内；没有观察或资料依据的状态不说成事实，不保证物品安全。检索块、`lookup_knowledge`、`observe_scene` 都没有相关条目时直接说明资料库没有，不得声称“资料里说”。
- **每轮资料检索**：`EmbodiedAgent` 收到真实用户输入时，按原话调用一次 `lookup_knowledge`，以 `RETRIEVAL_PREFIX` 块（紧凑渲染，最多 3 条，无结果为“资料库没有相关条目”）放在本轮状态之后、本轮问题之前；本轮问题仍是工具历史前最后一条人类文本。模型仍可自行调用 `lookup_knowledge` 查其他主题。
- **朗读清理**：新增 `speech/text.py` `speakable_text`，去掉标题、列表符号（“1. ”须带空格，行首“3.5 米”保留）、加粗、链接、代码块与表情，换行改为停顿；`SpeakSkill` 播报前清理，清理后为空则动作失败，证据记录 `spoken_text`。显示给前端的回答文本不改。
- **测试**：新增 `tests/test_speakable_text.py` 4 项（含真实模型回答原文）；`test_object_safety_flow.py` 消息顺序测试按新顺序更新，新增安全问题检索命中断言与系统提示规则断言。Ruff、格式检查通过，basedpyright 0 errors / 0 warnings，323 项 unittest 通过。
- **真实模型复测**（示例桌面图片作文件摄像头）：6 个问题回答均为纯文本 33–58 字；插座、剪刀回答内容与资料条目一致；资料库没有条目时直接说明；闲聊不硬扯资料；首问约 3 秒（模型已热），其余约 1 秒。仍为单轮抽样，非系统评测。

## 2026-10-27：全量代码审查与逻辑修复

按负责人“检查所有代码及逻辑，有问题就修改”的要求，通读 HTTP 服务、核心门面、Runtime、Agent、语音、感知与底盘代码。

- **记录终身上限（长期运行失效）**：任务、动作、宿主输入、转写、录音会话、播报输出各有 4096 条上限且从不清理，后端累计约 4096 次回答后所有动作提交失败，机器人不可再用。现 `InMemoryStore` 满额时清理最早的已结束任务及其动作（未结束的永不清理，全部未结束才拒绝）；宿主输入去重保留最近 4096 条，已验收任务的重放仍返回原结果；转写去重在任务终态时清理；语音会话与输出满额时清理已结束记录。Runtime 动作完成事件、任务取消作业在终态后移出登记；HTTP 层清理任务记录时一并撤销其播报关联与证据公开，积压的旧动作事件不再使事件转发协程崩溃。README 的保留策略说明同步更新（原为“满额明确拒绝，需重建应用”）。
- **真车运动必然超时**：模型提交动作默认期限 10 秒，底盘 1 米 / 0.1 m/s 本身需要 10 秒以上，`_drive` 自身期限为估算 × 2 + 3 秒。新增 `RobotAdapter.motion_timeout_s` 与 `RobotSkill.minimum_timeout_s`，Runtime 提交时把较短的期限放宽到设备所需；模拟器返回 0，不改变原期限。
- **编码器静止抖动锁死执行层**：正交编码器停在边沿会来回跳 1 个计数，原实现任一非零增量即判定“仍在转”，停车确认失败后 Runtime 永久锁定。现单帧增量不超过 1 计数视为静止。
- **串口并发写**：取消运动时停止帧在事件循环线程直接写出，线程池中的速度帧可能尚未写完，两帧字节交错。`CarLink.send` 与序号分配加线程锁。
- **模型上下文随对话增长**：每次回答产生一条播报动作，整条记录（约 546 字）进入每轮“本轮状态”，同一任务七八轮后超出 4096 上下文，Ollama 从开头截断会丢掉系统提示。本轮状态现在去掉播报动作（回答已在对话记忆中），只带最近 3 个设备动作；`get_snapshot` 本身不变。
- **GPT-SoVITS 走系统代理**：HTTP 客户端未设 `trust_env=False`，设置了 HTTP 代理的环境中请求本机或隧道地址会经代理返回 502（与本地模型客户端不一致）。已关闭环境代理。
- **关闭期间请求**：服务关闭时先摘下 `ServiceSlot.service`，关闭过程中到达的请求得到 50002。
- **测试**：新增 `tests/test_record_retention.py` 3 项（满额清理已结束任务及动作、进行中任务的动作不清理、上下文不含播报且只留最近 3 个动作）；`tests/test_car_adapter.py` 新增 1 项（短期限按设备放宽、编码器抖动下仍确认停车并成功，修复前失败）；固件替身新增 `jitter` 开关。
- **验证**：Ruff、179 个文件格式检查通过；basedpyright 0 errors / 0 warnings / 0 notes；352 项 unittest 通过；`app.robot_audio_demo` 离线运行成功。
- **未实测**：真车上的编码器抖动幅度、停车耗时与新期限。

## 2026-10-27：单条输入失败不再结束整个任务

按负责人“改成功能正常的”指示，处理上节留待决定的两项。原行为：模型报错、模型返回不合规工具调用、动作失败、连续动作达上限、播报失败，宿主都会取消整个任务，前端必须新建任务才能继续，GPT-SoVITS 慢时播报超时会频繁中断对话。

- **宿主**：`RobotHost` 在上述情况下只作废本条输入——取消并等待本条输入提交的动作，调用 `DecisionAgent.abandon_turn()` 清空等待中的动作与工具历史（对话记忆保留），任务继续接受下一句。只有用户停止、宿主关闭，或作废后执行层因停止确认失败而锁定（`blocked_reason`）时才结束整个任务，安全约束不变。回答文案改为“动作未成功（…），已停下，可以换个说法再试。”“已连续执行多个动作，先停在这里；不代表整体目标已完成。”“……（语音播报未完成，文字回答已保留。）”。
- **HTTP**：消息拒绝原因按任务是否仍可用给出“……，本条未执行，可以继续对话”或“……，本条未执行”；任务仍在时的 `ValueError`（模型不合规的工具调用或参数）记 50001“模型回答无法执行”，只有任务确已停止才记 40010。控制台提示改为“本条处理失败”。
- **测试**：原“失败即停止任务”的 4 项测试改为断言任务保留且下一句正常处理（运动失败、动作上限、合成失败、模型不可达 50002）；新增 2 项：模型调用不存在的工具时本条 50001、下一句正常回答；运动失败且停车无法确认时执行层锁定、任务记 failed。
- **文档**：`docs/COMPETITION_ARCHITECTURE.md` 宿主流程同步。
- **验证**：Ruff、179 个文件格式检查通过；basedpyright 0 errors / 0 warnings / 0 notes；354 项 unittest 通过；`app.robot_audio_demo` 离线运行成功。

## 2026-10-27：模拟底盘空闲后播报与移动全部失败

用迁到 v1.0 的前端连本机 `app.robot_server`（模拟底盘 + 模拟语音 + 本地假模型）联调时发现：回答已生成，`speak` 动作失败，原因 `Robot telemetry is stale or predates the command`。`SimulatedAdapter` 只在连接、移动、停止时更新 `updated_at`，空闲超过 `telemetry_max_age_s`（2 秒）后状态被判为过期，所有技能的前置检查都会失败；单元测试都在 2 秒内跑完，之前真模型联调未开语音，所以一直没暴露。

- **修改**：模拟器状态即时可得，`get_state` 以读取时间作为遥测时间，删去 `_updated_at`。
- **测试**：`tests/test_robot_adapter.py` 新增空闲超过遥测有效期后仍能执行转向（修复前失败）。
- **验证**：Ruff、格式检查通过；basedpyright 0/0/0；全部 unittest 通过。

## 2026-10-27：语音与前端分支审查（feature/speech-output-lifecycle）

分支在 PR #2 之后又积累 28 个提交：前端接设备真实数据、局域网设备发现、macOS 桌面端、Jetson 设备端指南、TTS/ASR 测评工具、音频设备按名称解析。按负责人指示审查后合入。

- **合并 main**：`API_CONTRACT.md` 按 v1.0 规定以 main 为准，分支上的 v0.2 草案修改不保留；`frontend/README.md` 冲突合并后按 v1.0 重写。
- **前端接口迁到 v1.0**：原 `DeviceClassroomService` 调用的 `tasks/{id}/session`、`tasks/{id}/inputs`、`POST tasks/{id}/observations`、`camera/stream` 与 `teaching.updated`、`input.updated`、`assistant.message`、`playback.updated`、`camera.status` 事件在 v1.0 中都不存在，连 main 的后端会全部失败。重写为任务快照（`GET /tasks/{id}`）+ SSE（带快照游标建流、`stream.reset` 重读快照，连续重置按 3 秒退避）+ 任务/消息/录音命令；请求体只含合同字段（服务端 `extra="forbid"`）；路径统一取 `core/constants/api_paths.dart`。v1.0 没有的能力：课堂阶段由消息处理状态推导（讲解中 / 等待提问），播报状态取最近一个 `speak` 动作，“刷新观察”改为一句提问请机器人重新观察，摄像头在没有 MJPEG 时各平台轮询单帧快照。被拒绝的用户消息补一条机器人说明并写入设备说明，避免页面一直停在“等待回答”。
- **原有问题**：`SizeTransition(alignment:)` 在 Flutter 3.38.2 不存在，改为 `axisAlignment: -1.0`（两个版本都能编译）；`BubbleSendTransition` 的动画控制器为惰性 `late final`，从未播放的实例在 `dispose` 中才创建 ticker 触发断言，改在 `initState` 创建；`dart format`。
- **测试**：`test/device_classroom_service_test.dart` 按 v1.0 重写 12 项（错误包装、快照映射、播报状态、建任务字段与游标建流、SSE 去重与回答补全、拒绝说明、`stream.reset`、网络不可达、摄像头地址、消息与录音命令字段）。
- **联调**：本机以模拟底盘 + 模拟语音 + 本地假 Ollama 启动 `app.robot_server`（端口 8090），迁移后的数据层实际走通建任务、文字问答与播报、录音转写问答、停止确认；模型不可达时消息被拒并显示原因，任务保留（PR #16 已合入）。联调中发现模拟底盘空闲后播报全部失败，已在 PR #16 修复。
- **验证**：`flutter analyze` 无问题，85 项 `flutter test` 通过，`flutter build web` 与 `flutter build macos` 成功；Python Ruff、188 个文件格式检查通过，basedpyright 0/0/0，371 项 unittest 通过。
- **未处理**：未与 Jetson 实机联调；后端没有跨源响应头，Web 版须与后端同源托管；页面没有“确认完成”入口；`pubspec.lock` 保留作者版本（本机 Flutter 3.38.2 解析结果较旧，未提交）。

## 2026-10-27：控制台原型分支审查（feature/robot-console-ui）

- **分支无法编译**：10-14 加入的根目录 `.gitignore` 规则 `models/` 未锚定，忽略了所有名为 models 的目录，作者的 `frontend/lib/features/robot_console/models/robot_console_models.dart` 从未进入提交，`flutter analyze` 144 个问题。main 已改为 `/models/`（`a0232db`）。按负责人指示，依据控制器、模拟服务、组件与测试中的用法重建该文件（9 个类型：`RobotSession`、`RobotStatus`、`ChatMessage`、`RobotAction`、`SceneObservation`、`SceneObject`、`SafetyEntry`、`ConsoleScenario`、`RobotConsoleFailure`），文件头注明为重建，不是原件；作者如保留原件可对照替换。
- **原有问题**：测试中 `final RobotAction actionOfState() =>` 不是合法 Dart；4 项测试使用默认 900 ms 步长却只推进 500 ms；3 项测试结束时遗留模拟定时器；窄屏布局用懒加载 `ListView`，视口外面板不构建，改为与中屏一致的 `SingleChildScrollView`；8 个文件未按 `dart format` 格式化。
- **验证**：`flutter analyze` 无问题，41 项 `flutter test` 通过，`flutter build web`（课堂与控制台两个入口）成功；Python 四项检查通过。控制台仍只使用模拟数据，未接 HTTP。

## 2026-10-28：真实模型、视觉、识别与合成整链路联调

环境：3080（silencecf.me）上 qwen3.5:9b（Ollama 11435）与 GPT-SoVITS api_v2（9881，`TORCH_CUDNN_V8_API_DISABLED=1` 已生效），本机经 SSH 隧道 21435/29881 访问；本机 `app.robot_server` 用模拟底盘、文件摄像头（桌面示例图）、SenseVoice 常驻识别、GPT-SoVITS 合成、MacBook 扬声器播放，语音提问经 BlackHole 虚拟声卡回环注入。按负责人指示停掉了占 15 GB 显存的 ComfyUI（`chengfang` 账号进程），qwen 由 98% CPU 回到 100% GPU，生成约 70 token/s。

发现并修复：

- **模型几乎从不调用工具（P1，10-25 引入）**：“回答格式”一段（“最终回答会念给孩子听。只写口语纯文本……”）让模型把每轮都当成文字回答。实测“看看桌上有什么”“向左转一点点”“往前走二十厘米”三类请求 0/9 调用工具，只回“好的，我正在转”。逐段消融确认唯一原因是该段；限定范围、移到末尾等改写最多 4/5。改为两条普通规则（“没有依据的状态不说成事实、不保证安全”“讲给孩子听的话用短句，不用列表、标题或表情”），并新增“孩子要求转向或移动时，本轮必须用 submit_action 提交动作；没有提交动作就不能说‘正在转’‘已经走了’”（长对话中移动请求仍会偶发只答应不执行，加此句后 6/6）。5 类问题合计由 6/15 变为 15/15，闲聊与安全问答不误触发动作。测试锁定系统提示不得再出现“纯文本”。
- **播报期限固定 30 秒**：合成约 0.13 秒/字、播放约 0.19 秒/字，七八十字的回答必然超时。宿主按朗读文本给出期限（15 秒 + 0.5 秒/字，另加 5 秒清理），`SpeakSkill` 以同一预算交给输出服务；显式给出的动作期限仍按原值执行。
- **GPT-SoVITS 重启后首句极慢**：服务重启后首次合成实测 88 秒。`GPTSoVITSProvider` 新增可选启动预热（主应用 180 秒期限），预热失败只记警告不阻止启动；本次预热 2.55 秒。
- **GPT-SoVITS 连接复用失败**：api_v2 的 uvicorn 空闲 5 秒关闭连接，合成后播放数秒再请求时复用已关闭的连接，报 `Server disconnected without sending a response`，请求未到达服务端。合成客户端不再保持空闲连接。
- **停止已恢复的任务记为 failed**：PR #16 让失败轮次可恢复后，之前轮次的失败播报仍让后续停止记为 failed、验收记为 failed。`TaskCoordinator.acknowledge_failures` 登记宿主已作废轮次中的动作，停止与验收不再因它们记失败；宿主未处理的失败（如课堂宿主因动作失败结束课堂）与执行层锁定仍记 failed。

最终整链路（`fullrun3`）：建任务、摄像头快照、观察（杯子左/书中/盒子右）、杯子追问、语音提问（转写一字不差）、左转并重新观察、前进 20 厘米并重新观察、插座安全问答、停止，8 个动作全部 succeeded，任务 cancelled。每轮耗时 13.5–39.3 秒，其中模型与观察 1.5–9 秒，其余为合成与播放（单句 7.8–32.2 秒）。

- **测试**：`test_robot_audio.py` 合成失败用例追加“恢复后停止记 cancelled”，新增“恢复后验收记 completed、播报期限随长度增加”；`test_gpt_sovits_tts.py` 新增预热一次并释放音频、服务不可用不阻止启动；系统提示测试同步。Ruff、190 个文件格式检查通过，basedpyright 0/0/0，380 项 unittest 通过。
- **回答变长与播报耗时（后续修复）**：去掉字数上限后回答约 40–120 字。试过的方案：系统提示加“一般不超过三句、80 字以内”使回答字数中位数由 63 降到 26，但“往前走二十厘米”又变为只答应不执行（6/7），不可用；回答后再用模型压缩会改变语气并编造内容（把桌面物体说成“资料库显示”、添加原文没有的提醒），每次多 1–4 秒，弃用。最终改为**分句流水线播报**：`SpeechOutputService` 把 30 字以上的朗读文本按句末标点切段（短于 12 字的句子并入下一句），播放当前句时合成下一句，首句沿用输出编号、后续句段编号为 `<编号>#<序号>`，全部完成后汇总终态；停止时停当前句段、取消预取合成并在确认后释放全部音频；任一句段失败或被停止即结束。短文本保持整段单次播放。实测（两端热状态）播报由约 0.32 秒/字降到 0.21–0.26 秒/字，开口前等待由整段合成的 6–13 秒降到首句合成约 1.2–3.1 秒；回答内容不变，工具调用不受影响。
- **模型常驻时长**：Ollama `keep_alive` 由 5 分钟改为 `OLLAMA_KEEP_ALIVE`（默认 30m）。联调中两轮间隔超过 5 分钟后 qwen 被卸载，下一句重新加载与 GPT-SoVITS 换页同时发生，出现一次模型超时（消息 50002）和一段 50 秒的合成。
- **3080 环境**：内存 31 GB、交换区常用 3–4 GB，GPT-SoVITS 闲置半小时后首句可能再次需要数十秒（本次预热 82 秒）；预热只在后端启动时执行，长时间闲置后的首句仍可能偏慢。
- **测试（续）**：新增 `tests/test_speech_pipeline.py` 6 项（短文本不切、按句切且拼回原文、无句末标点不切、下一句在本句播完前开始合成且汇总完成并释放音频、第二句合成中停止只播一句且音频释放、后续句段合成失败不假报完成）；`test_local_models.py` 新增常驻时长默认与覆盖。387 项 unittest 通过。最终整链路（`fullrun5`）8 个动作全部成功、任务 cancelled，每轮 11.5–31.7 秒。
- **未处理**：真车、真实摄像头与 Jetson 部署仍未联调；3080 长时间闲置后的首句延迟取决于该机内存。

## 2026-10-28：Jetson 部署准备（未上板）

- **背景**：Jetson 不在线（本机在外网，经 3080 也找不到原地址 192.168.0.127；在 3080 上扫描局域网被权限拒绝，未继续）。先准备不依赖板子的部署材料。
- **指南重写**：`docs/JETSON_DEVICE_GUIDE.md` 原版（10-20）描述“Jetson 只采音放音、经 SSH 把音频交给服务器、运行 `speech.real_demo` 固定回答、ESP32 运动无代码”，与 API_CONTRACT v1.0 的部署形态不符。改为：Jetson 运行 `app.robot_server`（Agent、动作、HTTP/SSE、摄像头、SenseVoice 常驻识别、STM32 底盘 CarAdapter），3080 的 Ollama 与 GPT-SoVITS 只监听本机、经 SSH 隧道访问。保留 10-20 实测的硬件与声卡事实、编码规范与汇报格式；补充 uv 免 sudo 安装、需请示的系统操作（libportaudio2、dialout、linger）、底盘标定、10 项验收清单与已知问题。
- **部署文件**：`deploy/jetson/robot.env.example`（Jetson 全部配置，端口 8090 与前端默认一致；含空格的值加双引号，systemd 与 bash 均可读取，已用 bash 实测解析）、`teaching-robot-tunnel.service`（用户级 SSH 隧道 21435→11435、29881→9881，保活与失败重启）、`teaching-robot.service`（用户级后端服务，依赖隧道）。
- **`.env.example`**：GPT-SoVITS 注释仍写“运行在 Jetson 本机 9880”，按合同改为 3080 的 9881、经隧道访问。README 增加指南入口。
- **核对**：锁文件中 sherpa-onnx、onnxruntime、opencv、av 均有 linux aarch64 wheel；`providers.audio.worker devices` 子命令存在。
- **未验证**：整套步骤未在 Jetson 上执行；systemd 单元未经 `systemd-analyze verify`（本机为 macOS）；Jetson 到 3080 的免密钥登录、局域网地址与声卡名称需上板确认。

## 2026-10-29：合并 #19、#20，Jetson 真实合成整链路

- 按负责人指示合并 [PR #19](https://github.com/SCMU-AILAB/teaching_robot_agent/pull/19)（`c9f6983`）与 [PR #20](https://github.com/SCMU-AILAB/teaching_robot_agent/pull/20)（`f128e81`，合并前改为以 main 为目标），删除两个分支。
- 按负责人指示清空 3080 显存（结束占用 16 GB 的 ComfyUI），恢复 GPT-SoVITS 服务与开机自启。
- Jetson 经负责人提供的远程 SSH 入口登录（只开放 SSH），部署 main `f128e81`。一条 SSH 会话同时把 Jetson 的 8090 转到本机、把本机到 3080 的隧道反向转到 Jetson 本机 21435/29881（临时中转，会话结束即失效）。GPT-SoVITS 刚重启，后端预热 30 秒。
- 整链路（模拟底盘、文件摄像头、PipeWire 播放到 C-Media 扬声器）：快照、观察（杯子左/书中/盒子右）、杯子追问、左转与前进各自提交成功并重新观察、插座问答，5 次播报全部 succeeded（24–87 字，7.9–21.2 秒），停止 → cancelled，执行层未锁定；每轮 12.4–30.9 秒。
- 观察到的模型行为：前进后回答“现在离桌子更近啦”，属于把短时移动说成接近物体，系统提示已有相应约束，未改。
- 未验证：麦克风与摄像头（未接）、真车、扬声器实际音量（PipeWire 0.40、ALSA 14/37 未调）、Jetson → 3080 隧道的正式授权与常驻服务安装。

## 2026-10-30：Jetson 接入真车与扬声器，底盘停车滑行修复

- **设备**：Jetson 识别到底盘串口（1a86:55d4，`/dev/ttyACM0`）与 C-Media 扬声器；摄像头与麦克风在 USB 上没有枚举（内核无插入记录），本次仍用文件摄像头、无语音输入。C-Media 只有播放端点。
- **扬声器**：开机后插入的声卡，WirePlumber 未启用输出配置，默认输出为 Dummy Output；重启 WirePlumber 后恢复。负责人反馈声音很小：PipeWire 输出 0.40、硬件 PCM 14/37，均调到 100%。
- **3080**：显存被负责人账号的 llama-server 占满，GPT-SoVITS 因显存不足反复重启 3178 次；经负责人同意清空后用 `model_server.sh start` 启动。
- **底盘标定**（负责人在车旁测量）：前进 1812 计数 / 10 cm → `CAR_COUNTS_PER_METER=18120`；转向 1754 计数 / 30° → `CAR_COUNTS_PER_RADIAN=3350`；横移未标定。
- **前进 20 cm 验收失败（P1）**：逐帧记录显示，车保持约 10 cm/s，直到距目标 0.23 cm 才停车；发出停止后又滑行 3.3 cm（固件占空为 0 时两路均低、不制动），最终 23.1 cm，超出 2 cm 容差。减速曲线只在最后 1.7 cm 生效，落地 PI 响应约 0.4 秒，来不及减速。
  - 修改：`CarAdapter` 按固件时间戳由相邻遥测求车体速度，以 `CAR_COAST_COUNTS_PER_S2`（默认 2700，实测）估算停车滑行量，提前发停车；前进与转向同用。停车确认期限由 1 秒放宽到 2 秒（最高车速滑行约 0.9 秒，原期限可能误报停车失败并锁定执行层）。
  - 固件替身新增停车滑行与轮速一阶响应；新增“真车标定 + 滑行”回归测试（前进 0.2 米、转向 0.3 弧度均在容差内），修复前失败、报错与真车相同。
  - 真车复测：前进停在 19.97 cm、后退停在 −20.04 cm（里程计）。
- **整链路**（真车、文件摄像头、GPT-SoVITS、扬声器）：观察、追问、左转 0.3 弧度、前进 20 cm、插座问答、停止，7 个动作全部 succeeded，任务 cancelled，执行层未锁定；每轮 15.3–35.6 秒。
- **问题**：Jetson 的公网 SSH 入口每隔几分钟断开，经它中转的模型与合成请求随之失败（一次播报超时即此原因，另一次为 3080 合成偶发 13–16 秒）。联调脚本改在 Jetson 本机运行；正式部署需 Jetson 直连 3080 的隧道。
- **验证**：Ruff、格式检查通过，basedpyright 0/0/0，388 项 unittest 通过。
- **未验证**：摄像头、麦克风（未识别）、横移、拔线看门狗停车、实际位移与里程计的误差（标定精度受尺量与目测限制）。

## 2026-10-31：Jetson 真实摄像头与麦克风

- **连接**：改经局域网 SSH 访问 Jetson，不再经公网入口；3080 仍经本机 SSH 隧道反向转到 Jetson。
- **设备**：Jetson 重启后识别到 RealSense D435i（`/dev/video0`–`5`，彩色画面为 `/dev/video4`，YUYV 640×480）与 Earpods（有采集）。WirePlumber 曾把 Earpods 选为默认输出，PipeWire 输出音量重启后回到 0.41；已指定 C-Media 为默认输出、Earpods 为默认输入，音量重设为 100%。部署模板 `PERCEPTION_DEVICE` 改为 4，指南补充核对方法。
- **摄像头整链路**（真车、RealSense、GPT-SoVITS、扬声器）：观察识别出小方凳（中间）、塑料收纳箱（左侧）、蓝色椅子（右侧）、置物架、办公椅，与画面一致；两次播报 succeeded，任务 cancelled，执行层未锁定。每轮 36–42 秒，其中播报 25–34 秒。
- **麦克风**：静音环境 RMS 约 2；扬声器放 800 Hz 测试音时录到 RMS 约 4800，Earpods 采集增益已为 100%。语音回环（GPT-SoVITS 合成提问经扬声器播出、后端录音识别）：“你好，你面前那个小凳子能站上去吗？”转写一字不差，回答与播报成功。尚未用真人说话验收。
- **观察到的模型行为（未改）**：回答常带空行并超过 100 字；追问时会把上一轮的观察描述重复一遍。
- **带真实画面的运动**（负责人确认前方有空地）：观察 → “向左转一点点” → “往前走二十厘米”，左转 0.3 弧度（2.3 秒）、前进 0.2 米（3.4 秒）各自成功并重新观察，3 次播报 succeeded，任务 cancelled，执行层未锁定；里程计终点航向 0.281 弧度、位移 0.200 米。每轮 18–24 秒。前进后回答仍有“离前面的物体更近啦”。
- 本次未改 Python 代码。

## 2026-10-31：回答提速与自由探索、深度避障

- **回答篇幅**：系统提示加字数限制会让模型少调用动作工具（实测 18/24）。改为工具调用与否仍由原提示决定；流式输出的文字超过 `AGENT_ANSWER_CHARS`（默认 40）的 1.5 倍且没有工具调用时，中断并追加篇幅要求重新生成。真实模型三套对话中工具调用 30/30 正确，回答字数中位 28–38、最长 53（原 62–77、最长 226）。副作用：回答常不再说明资料来源，偶有“这些都是安全的家具”，未处理。
- **先开口**：分句播报首段在第一个不短于 8 字的逗号或句末处切出。GPT-SoVITS 每段约 0.6 秒固定开销、每字约 0.035 秒（Jetson 实测）。真车整轮由 18–42 秒降到 7.7–16.2 秒，回答生成后约 1 秒开口。
- **转向方向（原有缺陷）**：`turn_relative` 参数说明只写“逆时针为正”，模型右转给正数（10 次错 3 次）；改为写明左正右负并举例。
- **只答应不执行**：回答声称已经或正在转向、移动，但本条输入没有提交运动动作时，追加纠正要求重答一次（`MOTION_CLAIM_CORRECTION`）。
- **视觉输出重试**：真车转向后重新观察时视觉输出偶发不合约定，整条消息被拒（50001）；校验规则不变，附上格式要求重试一次。
- **深度避障与探索**（设计见 `docs/superpowers/specs/2026-10-31-exploration-obstacle-design.md`）：
  - `perception/depth/`：经 V4L2 读 RealSense Z16（`linuxpy`，新可选依赖组 `depth`），按安装高度与俯仰去地面，计算车身通道（车宽 0.25 + 余量）内 5 个方向的空隙；`estimate_mount` 在空地上拟合地面得出高度、俯仰与盲区。
  - `robot/obstacle_guard.py` `GuardedRobotAdapter`：前进每 0.1 秒检查空隙，不足停车距离加滑行量、深度过期、贴近后读数骤减时停车并抛 `ObstacleError`；后退限 0.3 米；深度启动失败时前进一律拒绝。
  - `agent/exploration.py`：按空隙、新格子与离起点半径选方向，一段段走，停下观察，新物体（名称互相包含视为同一个，资料库有条目的优先）由模型讲一句，结束时总结。
  - 宿主：模型工具 `start_exploration` 登记请求，本条回答播完后在后台探索；新输入、停止任务、关闭时先停探索。`DEPTH_CAMERA=off`（默认）时不包装、不开放探索。
- **测试**：新增 `test_answer_length`、`test_motion_claim`、`test_depth_geometry`（合成深度图）、`test_obstacle_guard`、`test_exploration`。Ruff、格式检查通过，basedpyright 0/0/0，全部 unittest 通过。
- **真机**：深度节点可读（640×480 约 24 帧/秒，有效像素 76%）。D435i 加速度计在 `/dev/hidraw3`，仅 root 可读，未使用。负责人调整摄像头后标定三次：离地 0.260–0.265 米、俯仰 -4.7°～-5.3°（朝下）、盲区 0.35 米；`watch` 读数与画面一致，地面未误判。
- **真车避障**：正前方 0.97 米有办公椅，“往前走一米”前进 0.48 米停车（距椅脚约 0.49 米），动作 failed、原因“前方有障碍物”，执行层未锁定。
- **真车探索首测失败**：模型对“我们去探索一下吧”只回答“我要出发了”，不调用工具；这句话进入对话记忆后，后续回答照抄，连其他工具也不再调用（本机评测 2/8）。加系统规则、给工具加参数后最常用说法仍不调用，纠正重答无效。修复：
  - 宿主按用户原话识别明确的探索请求（“去探索”“到处逛逛”“四处转转”“自己走走”等，排除“什么是探索”），直接回答 `EXPLORE_REPLY` 并在播报后开始探索，不经模型；
  - 未覆盖的说法仍由模型调用 `start_exploration(plan)`，系统提示仅在探索开放时加入 `EXPLORATION_RULE`；
  - “开始探索”“要出发”等说法纳入只答应不执行检查，探索开放时纠正语提示可调用 `start_exploration`。
  - 评测（开放探索）：探索说法 8/8，常规三套 27/30。仍会出错的：“帮我看看周围都有啥”被答成“我现在开始探索房间”而不调工具，纠正一次后仍如此。
- **后退上限**：`RobotAdapter.max_reverse_m`（守护设为 0.3）写进 `move_relative` 参数说明并在提交时校验；此前模型请求 -0.5 米失败后，把失败原因套到下一句前进上并拒绝前进。
- **失败说明**：动作失败时宿主回答说明原因（遇障为“前方有障碍物，已停下，换个方向试试吧。”）并播报；原先只有文字“动作未成功（failed）”。
- 432 项 unittest 通过，Ruff、格式检查通过，basedpyright 0/0/0。真车探索复测待中转恢复后进行。
