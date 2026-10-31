# 验收测试报告

日期：2026-10-31  
平台：Jetson Orin NX，Ubuntu 24.04，aarch64，GCC 13.3，CMake 3.28.3  
后端：`ROBOT_ADAPTER=car`、`ROBOT_PERCEPTION=live`、`ROBOT_AUDIO=real`、端口 `8090`

## 构建

执行：

```bash
cd /home/qwq/teaching_robot_agent/robot-ui
rm -rf build
./scripts/build_jetson.sh
```

结果：

- `.deps` 免 root 补齐 X11 头文件成功。
- CMake 仅检测 C 编译器，不再出现 CXX 检测。
- `robot_ui` 链接为 ARM aarch64 ELF。
- `robot-ui/src` 与 `robot-ui/tests` 无编译告警；`-Werror` 开启。
- Raylib 5.5 第三方源码自身存在 `-Wunused-result`/`-Wstringop-overflow` 告警，未使用 `-Werror` 扩展到第三方目标，未修改其源码逻辑。

Raylib 上游 CMake 默认会启用 CXX。构建脚本仅将 `project(raylib)` 替换为 `project(raylib C)`，cJSON 直接编译 `cJSON.c`，因此本项目所有业务源文件均为纯 C。

## CTest

```text
1/3 Test #1: test_config ... Passed
2/3 Test #2: test_state .... Passed
3/3 Test #3: test_text ..... Passed
100% tests passed, 0 tests failed out of 3
```

覆盖：

- 默认配置、文件配置与非 HTTP 地址拒绝。
- UTF-8 中文/emoji 追加、退格、长度。
- 系统状态、任务快照、消息事件、重复 sequence 拒绝。

## 真实后端联调

| 操作 | 证据 |
| --- | --- |
| 系统状态 | 后端 `/system` 返回 real car、live camera、audio ready |
| 创建任务 | 按钮点击使 `active_task_id` 从 null 变为 2 |
| 文字输入 | 点击输入框键入 `hi`，点击发送后后端收到 `user/hi` 并生成 assistant 回答 |
| 任务取消 | 点击停止任务，后端 `#1` 进入 `cancelled` |
| 语音 | 点击开始录音，后端返回 `recording`；再次点击后录音结束，ASR 自动生成 `source=speech` 消息，UI 未二次提交 |
| SSE | UI 运行期间由外部客户端提交问题，用户消息和 assistant 回答实时出现在界面，无重复 |
| 摄像头 | 后端返回真实 640×480 JPEG，界面显示实际桌面画面；无数据时不伪造帧 |
| 任务快照 | Monitor 显示任务 #1/#2、消息、动作和真实时间线 |
| 后端离线 | 指向未监听端口运行 4 秒，UI 正常渲染并显示 OFFLINE/LOST |
| 后端恢复 | 临时后端在 UI 启动后上线，UI 自动恢复 Connected 并记录恢复事件 |

测试期间修复的关键缺陷：二进制 JPEG 响应的 `api_code` 初值为 -1，曾被通用 JSON 成功判断误判为失败，导致摄像头永远不显示。现二进制 2xx 响应的业务码为 0，JSON 错误响应仍按 `code` 覆盖。

## 显示与交互

- Xephyr 1280×600：四页均可启动、渲染并抓取 1280×600 帧缓冲。
- 运行中将窗口由 1280×600 调整为 1024×600：等比缩放，letterbox，无重叠或裁切。
- `--fullscreen`：1280×600 全屏运行成功。
- 鼠标点击：通过直接向窗口发送 X11 ButtonPress/ButtonRelease，已验证底部导航、输入框、发送、语音、新建任务和停止任务。
- 物理屏幕：联调后期 `xrandr` 显示 `DP-1 disconnected`，无法继续做物理屏幕截图和真实触摸验收。
- 物理屏幕曾报告 1024×600，而非任务说明的 1280×600。软件按逻辑尺寸缩放，但硬件是否最终为 1280×600 仍需现场确认。

## 性能

采样命令：`ps -C robot_ui` + `tegrastats --interval 1000`。

Xephyr + Mesa llvmpipe 软件渲染下：

- RSS：约 190 MB。
- 进程 CPU：约 199%（4 个软件渲染线程，不代表 Jetson 实体 GPU）。
- GR3D_FREQ：约 21%（系统遥测，但窗口由 llvmpipe 绘制）。
- 连续运行 30 秒：无崩溃、无事件断流。

未能在物理屏幕连接的 NVIDIA GL 桌面下完成真实 GPU 占用、触摸滚动、长时间温升测试。

## 未验证清单

- 物理 1280×600 屏幕上的最终目视验收。
- 真实触摸面板的事件与手感（设备已注册为 `QDtech MPI7003`）。
- 中文输入法候选框与触摸软键盘。
- 观察证据图像（本次任务没有产生 observation）。
- 播报失败、识别失败等异常状态的真机注入。
- 真机 GPU 性能、长时间运行和温度。
