# 分支审查问题与修复记录

本文记录分支代码审查及修复；项目开发规范仍以 `AGENTS.md` 和 `docs/CODING_SPEC.md` 为准。

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
