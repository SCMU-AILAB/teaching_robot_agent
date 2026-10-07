# 非 main 分支提交审查

2026-10-07 已执行 git fetch origin。以远端最新提交为准，检查相对 main 合并基点的新增内容；不把 main 尚未提交的修复作为分支缺陷。未切换工作区、未修改分支业务代码、未提交或合并。

| 分支 | 审查提交 | 范围 |
| --- | --- | --- |
| origin/feature/perception-camera | d544e722747c72dc5cec552d03f4c61129d5a59d | 相机、工厂、场景存储、依赖及测试；包括最新采集锁修改 |
| origin/feature/speech-output-lifecycle | 61b70c9a17bf57f596249a0ba3043110392881b0 | 语音输入输出、模拟适配器、生命周期及测试 |
| feature/teaching-flow | 583713a | 无 main 之外的独有提交 |

本地 feature/perception-camera 停留在旧提交，远端审查覆盖其内容。refs/stash 不是开发分支，不纳入此次审查。以下行号对应表中不可变提交。

## 1. P1：USB 采集取消后，后台读取与设备释放并发

位置：`perception/camera/usb_camera.py:51`，关联 `perception/camera/base.py:54` 和 `usb_camera.py:61`。

`asyncio.to_thread(self._cap.read)` 的等待被取消不会停止底层线程，但 capture 会退出并释放 asyncio 锁。随后 close 可以在线程中调用同一 VideoCapture 的 release；下一次 capture 也可能启动第二个 read。最新提交的锁只能串行化协程，不能串行化取消后仍在运行的驱动调用。

已复现：用线程事件阻塞 read，取消 capture 后调用 close，release 在 read 尚未结束时进入，结果 `release overlaps outstanding read: True`。这会导致设备访问竞争；具体原生驱动可能失败或崩溃。

建议：显式保存并管理设备作业，取消等待者后仍须等驱动作业退出再释放锁和设备；为不可中断的驱动提供超时或独立设备线程策略。打开 VideoCapture 的 to_thread 也需要处理取消后的迟到句柄回收。

## 2. P1：连接途中调用 close 会直接返回，关闭后设备仍打开

位置：`perception/camera/base.py:74`，关联 `:39`。

connect 等待 open_device 时 `_closed` 仍是 True。此时 close 在获取锁前直接返回，之后 connect 完成并把状态设为打开。应用退出或取消初始化时，调用方认为已经关闭，实际仍占用相机。

已复现：阻塞 open_device，调用并等待 close，再放行连接；release_device 调用次数为 0，而且 capture 仍成功返回 `gate:1`。

建议：连接与关闭共同管理在途初始化状态，关闭等待初始化结束并释放资源；不要用初始化期间的 closed 标志跳过关闭。

## 3. P1：取消语音会话后，迟到 ASR 结果仍提交到任务输入队列

位置：`speech/input.py:113` 至 `:123`，关联 `_begin_cancel`。

输入服务只对识别任务发送 cancel，没有像输出服务一样保留不可逆取消标记并在外部调用返回后检查。如果 ASR 适配器在取消时收尾并返回文本，`_run` 会继续 submit_transcript，且把 cancelled 状态改成 completed。cancel 只取消语音会话，不取消核心任务，因此门面的任务状态检查无法阻止这条迟到输入。

已复现：ASR 等待期间捕获 CancelledError 并返回最终文本；调用 `await service.cancel('r')` 后，会话状态为 completed，核心队列实际收到“取消后仍提交的移动指令”。相同结构还缺少识别 deadline 已过期后的显式拒绝，适配器吞掉超时取消时可能接受过期结果。

建议：先设置会话取消标记；ASR 返回后、提交前检查取消、服务关闭及 deadline，迟到结果不入队。增加取消不合作 ASR 和迟到超时结果的回归测试。

## 4. P2：并发 connect 会重复打开设备

位置：`perception/camera/base.py:36` 至 `:40`。

两个 connect 可以在第一个 open_device 完成前同时通过锁外 `_closed` 检查。第二个取得锁后不再复查状态，因此再次调用 open_device。USB 实现随后覆盖 `_cap`，旧设备句柄没有显式释放，或第二次打开因设备被占用而失败。

已复现：同时发起两个 connect，并通过事件控制第一次打开完成，open_device 调用次数为 2。

建议：在锁内检查状态并复用同一初始化结果；补充并发连接只打开一次的测试。

## 5. P2：文件相机接受无法解码的图片并登记有效帧

位置：`perception/camera/file_camera.py:23` 至 `:31`，以及 JPEG SOF 提前返回路径。

PNG 仅检查前四字节和固定偏移宽高，不检查完整签名、块结构、CRC 或像素内容。JPEG 读到尺寸即返回，也不能证明剩余文件完整。grab_frame 随后直接登记这些字节为图像证据，损坏输入被推迟到视觉模型解码时才失败。

已复现：仅 24 字节、以四字节 PNG 前缀开头并填入宽高的无效文件，被解析为 `(1, 1, 'image/png')`，完全没有 IHDR/IDAT/IEND 有效内容。

建议：使用实际图像解码器校验并解码，再读取尺寸和格式；保留文件大小及像素上限。补充头部有效但内容截断、CRC 损坏等测试。

## 验证结果与限制

- 相机分支：Ruff、格式检查通过，basedpyright 0 errors / 0 warnings；97 项测试通过。
- 语音分支原提交：Ruff、格式检查通过，basedpyright 0 errors / 0 warnings；68 项测试通过。
- 另将语音新增补丁放到已提交 main 的临时副本检查兼容性，不包含当前 main 工作区未提交修复；Ruff、格式检查通过，basedpyright 0 errors / 0 warnings，108 项测试通过。
- 故障复现使用事件控制的模拟驱动和适配器，无真实摄像头、麦克风或远端模型调用。复现脚本保存在 `/tmp/repro_camera_branch.py`、`/tmp/repro_speech_branch.py`。

现有测试通过没有覆盖上述边界，建议修复后再合入。相机和语音目前都属于组件实现，尚未装配到真实设备主链路；这与各分支声明的范围一致，不作为本次缺陷。
