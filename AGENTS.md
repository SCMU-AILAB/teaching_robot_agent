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
