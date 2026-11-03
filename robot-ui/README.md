# Robot UI — Jetson 原生智能交互终端

`robot-ui/` 是 `teaching_robot_agent` 的板端显示前端。应用使用纯 C11、Raylib、raygui、libcurl、cJSON 和 pthread，面向 Jetson Ubuntu Desktop 的 7 英寸 1024×600 触摸屏，按 1024×600 逻辑画布布局；其他尺寸的窗口等比缩放。

它不是独立演示：所有状态、对话、语音、摄像头和任务事件均来自仓库现有 `API_CONTRACT.md` v1.0 HTTP/SSE 接口。接口审计见 [docs/INTERFACE_AUDIT.md](docs/INTERFACE_AUDIT.md)，完整实测记录见 [docs/TEST_REPORT.md](docs/TEST_REPORT.md)，接手开发请先读 [docs/HANDOVER.md](docs/HANDOVER.md)。

## 功能

- 对话（默认页）：开始聊天、大号语音按钮、四个快捷说法（看看周围 / 这能碰吗 / 讲讲安全 / 去探索吧，点一下即发送，不用打字）、主动提醒以黄框标出、手指上下拖动翻看历史、停止与完成。
- 总览：设备/服务状态、摄像头预览、当前任务、最近事件。
- 视野：实时画面、观察时的画面、观察摘要与物体方位。
- 监控：任务信息、时间线、系统诊断与最近错误。
- 待机表情：无操作 30 秒（`idle_timeout_s`）或点左上角小脸回到表情页，点屏幕进入界面。
- 触摸：同一帧内的按下与抬起会被拆成两帧（`src/touch.c`），快速点按不会丢失。
- 后台 HTTP 队列、独立 SSE 线程、断线重连、事件去重、后端重启检测。
- 中文字体回退、触摸目标约束、日志/消息/动作上限控制。

## 目录

```text
robot-ui/
├── CMakeLists.txt
├── config/
│   ├── default.json
│   └── jetson.json
├── include/                 # 应用、状态、网络、事件、页面接口
├── src/
│   ├── main.c
│   ├── app.c
│   ├── api_client.c         # libcurl + 单工作线程 + 有界队列
│   ├── event_client.c       # SSE + 独立线程 + 重连
│   ├── camera.c             # JPEG → Raylib Texture
│   ├── state.c              # API 合同数据模型与事件合并
│   └── screens/
├── assets/fonts/            # Noto Sans SC、Inter、字体许可证
├── scripts/build_jetson.sh  # 免 root X11 头文件补齐 + 构建 + 测试
├── tests/                   # CTest：配置、UTF-8、任务状态
└── docs/INTERFACE_AUDIT.md
```

## 系统依赖

Ubuntu 24.04 / JetPack 对应环境需要：

- CMake ≥ 3.20、GCC、pkg-config
- libcurl 开发包
- X11、Xrandr、Xi、Xcursor、Xinerama、Xfixes、Xrender 开发包
- OpenGL 与桌面环境
- **不需要 C++ 编译器**

有 sudo 时可安装：

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake pkg-config libcurl4-openssl-dev \
  libx11-dev libxrandr-dev libxi-dev libxcursor-dev libxinerama-dev \
  libxfixes-dev libxrender-dev libxext-dev x11proto-dev
```

没有 sudo 时直接使用仓库脚本。它会把缺失的 X11 开发包 `apt-get download` 到 `robot-ui/.deps/`，用 `dpkg-deb -x` 解包并作为本地 sysroot 使用，不会安装系统包：

```bash
chmod +x scripts/build_jetson.sh
./scripts/build_jetson.sh
```

Raylib 5.5、cJSON 1.7.18 与 raygui 4.0 由 CMake FetchContent 固定版本获取，因此首次配置需要网络。Raylib 的构建声明会被收窄为 `project(raylib C)`，cJSON 直接以 `cJSON.c` 编译；整个目标不启用 C++。

## 构建

已安装 X11 开发包时：

```bash
cd robot-ui
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j"$(nproc)"
ctest --test-dir build --output-on-failure
```

未安装系统开发包时，先使用 `scripts/build_jetson.sh`，或手动指定本地 sysroot（脚本会自动完成）。

## 运行

```bash
cd robot-ui
./build/robot_ui --config config/jetson.json
```

板端实测后端使用 8090：

```bash
./build/robot_ui --backend http://127.0.0.1:8090
```

通用参数：

```text
--config PATH          配置文件，默认 config/default.json
--backend URL          覆盖 base_url
--fullscreen           启动即全屏
--screen NAME          dashboard / assistant / vision / monitor（便于启动定位与验收）
--screenshot FILE      运行指定秒数后抓取帧缓冲并退出
--screenshot-delay SEC 自动截图延迟，默认 2.5
```

启动后：

- 设置里的“切换全屏”或 `F11` 切换全屏（顶部不放全屏按钮，避免触摸误点退出全屏）。
- 底部 总览 / 对话 / 视野 / 监控 触摸或鼠标切换。
- `1`–`4` 也可切换页面。
- `F12` 在程序当前工作目录生成 `robot_ui_capture.png`。
- 窗口可缩放，所有界面按最小比例等比适配；窗口缩小时不会改变逻辑布局关系。

> Raylib `TakeScreenshot` 只使用文件名，忽略路径；`--screenshot` 的文件会写到启动时的工作目录。

## 配置

`config/default.json` 使用合同默认后端 `http://127.0.0.1:8000`。板端部署使用 `config/jetson.json`（`8090`）。字段：

| 字段 | 说明 |
| --- | --- |
| `base_url` | Jetson 机器人后端地址，不包含 `/api/v1` |
| `window_width` / `window_height` | 初始窗口尺寸，默认 1024×600 |
| `target_fps` | 渲染目标帧率，默认 30 |
| `camera_fps` | 摄像头轮询频率，默认 3，允许 0.2–5 |
| `system_interval_s` | 系统状态轮询间隔 |
| `http_timeout_s` | 单次 HTTP 超时 |
| `font_body` / `font_display` / `font_mono` | 字体路径；缺失时按系统 Noto/DejaVu/默认字体回退 |
| `fullscreen` | 是否以全屏启动 |

后端必须监听板端可访问地址，例如：

```bash
ROBOT_API_HOST=0.0.0.0 ROBOT_API_PORT=8090 \
  uv run --extra speech --extra car --extra depth python -m app.robot_server
```

前端不要直接访问 3080 上的 Ollama 或 GPT-SoVITS；这些由 Python 后端调用。

## 交互与合同约束

- 语音识别由后端完成并自动生成 `source=speech` 的用户消息；UI 不重复提交转写文本。
- “停止任务”调用 `PATCH /tasks/{id}` 的 `cancelling`，不是硬件急停。仓库没有硬件急停 HTTP 接口，因此 UI 不展示伪急停。
- Vision 不绘制检测框，因为观察接口没有框坐标；`simulated=true` 显示 SIMULATION，`stale=true` 显示 STALE。
- Monitor 不绘制实时运动曲线，因为 API 没有连续高频遥测。
- 后端暂时不可用时 UI 继续运行并每秒重试系统状态；恢复后重新读取快照，进程实例变化时丢弃旧任务状态。

## 字体

- 中文：随仓库提供 `assets/fonts/NotoSansSC.ttf`，许可证 `OFL-NotoSansSC.txt`。
- 英文：随仓库提供 Inter 4.1 `Inter-Regular.ttf`，许可证 `OFL-Inter.txt`。
- 等宽：优先 `assets/fonts/DejaVuSansMono.ttf`，再找 `/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf`，最后回退正文字体。
- 缺少字体时不会阻止启动，但可能无法显示中文。

## 2026-11-03 车载屏适配

车载屏实为 1024×600。原界面按 1280×600 布局后整体缩到 80%，上下留黑边，字号 11–16 像素，触摸屏上看不清、点不准。改为 1024×600 原生布局：正文 18–20、按钮 20、导航 22 像素，按钮高 56–92；界面全部中文；去掉文字输入框，改为快捷说法；修复快速点按丢失与摄像头更新时间显示为 01-01 08:00。真机四页截图确认无重叠；真实手指触摸手感需在车旁确认。

## 验收结果（2026-10-31 实机，1280×600 旧布局）

| 项目 | 结果 | 说明 |
| --- | --- | --- |
| Jetson 干净构建 | 通过 | aarch64 GCC 13.3、CMake 3.28.3，从空 `build/` 完整重建 |
| 纯 C 检查 | 通过 | 无 `.cpp/.cc/.cxx`；CMake 只识别 C 编译器；业务目标以 `-Werror` 编译 |
| CTest | 3/3 通过 | 配置、UTF-8 输入、任务快照/事件去重 |
| 四页渲染 | 通过 | Xephyr 1280×600 抓取 Dashboard、Assistant、Vision、Monitor 帧缓冲 |
| 真实后端 | 通过 | `8090` 上系统状态、任务创建/快照/取消、文本消息、Agent 回答、TTS 动作 |
| 文本输入与发送 | 通过 | 点击输入框、输入 `hi`、点击发送，后端收到消息并生成回答 |
| 语音按钮 | 通过 | UI 发起录音，后端返回 recording；UI 结束录音后完成 ASR 并产生 `source=speech` 消息 |
| SSE | 通过 | UI 启动后由另一个客户端提交消息，界面实时出现用户消息与回答，无重复 |
| 摄像头 | 通过 | 真实 640×480 JPEG 按比例显示；断开时显示 STREAM ERROR |
| 后端离线/恢复 | 通过 | 离线时 UI 不崩溃并显示 LOST；后端恢复后自动显示 OK |
| 窗口缩放 | 通过 | 运行中从 1280×600 缩至 1024×600，控件不重叠不裁切 |
| 全屏 | 通过 | 1280×600 全屏运行并截图 |
| 30 秒稳定性 | 通过 | 无崩溃、无断流 |
| 物理屏幕 1280×600 | **未验证** | 最终阶段 `xrandr` 报 `DP-1 disconnected`；此前实测屏为 1024×600 |
| 真实触摸 | **未验证** | `QDtech MPI7003` 已注册为 X11 输入；未做物理触控复验 |
| 中文 IME | **未验证** | UTF-8 输入框可用；系统输入法弹出与中文候选未实测 |

性能采样在 Xephyr + Mesa llvmpipe 软件渲染下进行：RSS 约 190 MB，进程 CPU 约 199%，不能代表 Jetson GPU 桌面。物理屏幕断开后无法完成真实 GPU、触摸滚动和长时间温升验收，相关结论保持未验证。

## 常见问题

**CMake 报 `RandR headers not found`**  
使用 `scripts/build_jetson.sh`，或安装 `libxrandr-dev` 等 X11 开发包。

**窗口启动后立即崩溃，日志提示无法确定 Monitor**  
当前 X 会话没有已连接显示器。连接/唤醒屏幕，确认 `xrandr` 中显示器为 connected，并确认 `$DISPLAY` 与 `$XAUTHORITY` 指向桌面会话。

**摄像头区域显示 STREAM ERROR**  
后端 `GET /camera/snapshot` 失败，通常是相机尚未出首帧或设备被占用。UI 保留上一帧并显示错误，不会伪造画面。

**中文显示为方框**  
确认 `assets/fonts/NotoSansSC.ttf` 存在，或配置可用的 CJK TTF/TTC 路径。

**按钮点击位置偏移**  
程序会根据窗口尺寸设置逻辑坐标缩放和鼠标偏移；若使用远程 X11 转发，先确认窗口实际尺寸与输入设备映射。
