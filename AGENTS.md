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
