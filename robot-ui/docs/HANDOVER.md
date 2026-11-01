# Robot UI 交接文档

> 面向接手人。目标：30 分钟内能在自己的机器和 Jetson 上把它跑起来，并知道哪些地方不能碰、哪些坑已经踩过。
> 最后更新：2026-11-01

---

## 0. 一句话现状

`robot-ui/` 是一套**纯 C11 + Raylib 的原生机器人交互终端**，为 Jetson 7 寸屏（逻辑 1280×600）设计，通过 HTTP + SSE 与仓库现有 Python 后端通信。四页、真实数据、真实语音、真实摄像头都已在本机板端跑通并通过实测；**代码尚未推送到远端，物理屏 1280×600 与真实触摸未完成最终验收**。

---

## 1. 交接信息

| 项 | 值 |
| --- | --- |
| 分支 | `feature/robot-native-ui`（基于 `origin/main` @ `bcf14e4`，当前 ahead 1，**未 push**） |
| 提交 | `5542d4a` `feat(robot-ui): add native Raylib interaction terminal` |
| 本地工作区 | `/Users/silencecf/code/teaching_robot_agent/.workbuddy/worktrees/robot-ui`（独立 worktree，不干扰主工作区） |
| 板端部署副本 | `qwq@<jetson>:~/teaching_robot_agent/robot-ui` |
| 规模 | 33 个 `.c/.h`，3515 行；零 `.cpp/.cc/.cxx` |
| 技术栈 | Raylib 5.5 + raygui 4.0 + libcurl + cJSON 1.7.18 + pthread + CMake（`LANGUAGES C`） |
| 验证平台 | Jetson Orin NX / Ubuntu 24.04 / aarch64 / GCC 13.3 / CMake 3.28.3 |
| 后端 | 仓库现有 HTTP API v1.0，部署实例端口 **8090** |

**没有改动的东西**：Python 后端、`API_CONTRACT.md`、`MODULE_CONTRACT.md`、现有 Flutter `frontend/`、`runtime/`、`robot/`。本次只新增了 `robot-ui/`。

---

## 2. 先做这三件事

### 2.1 拿到代码

代码只在本地分支上，三种方式任选，**需要负责人决定**（见 §11）：

```bash
# A. 推分支（推荐，需有仓库写权限）
git push -u origin feature/robot-native-ui

# B. 打补丁包（不含 17MB 中文字体时需另行拷贝 assets/fonts/）
git format-patch origin/main..feature/robot-native-ui -o /tmp/robot-ui-patch

# C. 整包带走（含字体与文档）
git bundle create /tmp/robot-ui.bundle origin/main..feature/robot-native-ui
```

### 2.2 在自己的机器上构建

Linux（Ubuntu 22.04+/Jetson）可直接用脚本：

```bash
cd robot-ui
./scripts/build_jetson.sh          # 自动补齐 X11 头文件到 .deps/，无需 sudo
./build/robot_ui --help
```

macOS 只验证到「能配置」，未经联调；不要在 Mac 上做验收结论。

### 2.3 在 Jetson 上跑起来

```bash
# 后端（项目根目录，按现场配置）
cd ~/teaching_robot_agent && uv run python -m app.robot_server   # 端口 8090

# UI（另开终端，必须带图形会话环境变量）
cd ~/teaching_robot_agent/robot-ui
DISPLAY=:1 XAUTHORITY=~/.Xauthority ./build/robot_ui --backend http://127.0.0.1:8090
```

---

## 3. 代码地图

### 3.1 分层

| 文件 | 行数 | 职责 | 改动风险 |
| --- | --- | --- | --- |
| `src/main.c` | 94 | 参数解析、配置加载与校验、生命周期 | 低 |
| `src/app.c` | 564 | 主循环、请求调度、响应/事件分发、顶部栏、底部导航、设置抽屉 | **高** |
| `src/state.c` | 381 | JSON → `UiState`，事件去重、实例切换、事件环形缓冲 | **高** |
| `src/api_client.c` | 421 | 单工作线程 + 有界队列 + 响应队列（JSON/二进制） | **高** |
| `src/event_client.c` | 370 | SSE 长连接、断线重连退避、事件队列、游标 | **高** |
| `src/camera.c` | 95 | JPEG → `Texture2D`，live/evidence 双纹理，旧纹理释放 | 中 |
| `src/ui_common.c` | 201 | 按钮、徽标、状态行、输入框、文本裁剪、换行 | 中 |
| `src/theme.c` | 104 | 颜色表、字体加载与多级回退 | 中 |
| `src/config.c` | 127 | JSON 配置、默认值、校验 | 低 |
| `src/router.c` | 28 | 页面切换与 180ms 过渡 | 低 |
| `src/text.c` | 109 | UTF-8 追加/退格/截断（中文安全） | 低 |
| `src/screens/*.c` | 483 | 四页渲染与交互，只读 `UiState` | 低 |
| `tests/*.c` | 83 | config / state / text 三组断言 | — |

### 3.2 数据流（务必先看懂这条链）

```
启动
 ├─ GET /api/v1/system                 → state_apply_system
 │     └─ instance_id 变化 → 清空全部业务状态（后端重启，禁止重放旧事件）
 ├─ GET /api/v1/tasks/{id}             → state_apply_snapshot（消息/动作/观察/录音）
 └─ GET /api/v1/tasks/{id}/events      → SSE，Last-Event-ID: "{instance}:{sequence}"
        └─ sequence <= last_sequence 的事件直接丢弃（幂等）
        └─ task.updated / message.updated / action.updated
           / observation.created / recording.updated

循环（30 FPS）
 ├─ process_responses()   主线程消费响应队列 → 更新 UiState / 纹理
 ├─ process_events()      主线程消费 SSE 队列 → 更新 UiState / 事件游标
 ├─ schedule_requests()   1s 轮询 system；Dashboard/Vision 页 3fps 抓摄像头；证据图按需
 └─ draw_frame()          1280×600 逻辑坐标 + 等比缩放 + letterbox
```

**线程模型**：主线程只做渲染和状态写入；`api_client` 一个工作线程串行执行 HTTP；`event_client` 一个线程阻塞读 SSE。两者都用 mutex + cond + 有界队列把结果交回主线程，**任何后台线程都不调用 Raylib 绘图函数**。

### 3.3 用到哪些接口

已审计的 10 个接口全部接入（清单见 `docs/INTERFACE_AUDIT.md` §2）。其中 9 个已真机验证；`GET /evidence/{id}` 只在代码路径上跑过，因为验收期间没有产生 observation。

---

## 4. 红线（不可违反）

1. **纯 C**。不引入 C++、Qt/QML、ImGui。新增业务文件只能是 `.c/.h`。
2. **不改后端**。HTTP 增删改先改 `API_CONTRACT.md` 并经负责人确认；UI 不得编造字段、事件类型或业务行为。
3. **不伪造数据**。没有检测框就不画框；没有运动遥测就不画曲线；`simulated=true` 必须显式标注。
4. **不做伪急停**。后端没有硬件急停接口，「停止任务」就是 `PATCH /tasks/{id}`，不要包装成急停。
5. **不重复提交转写文本**。ASR 结果由后端生成 `source=speech` 消息，UI 只负责录音开关，再次提交会造成重复消息。
6. **不阻塞主循环**。任何网络、磁盘、解码操作都不得出现在渲染线程。
7. **`-Werror` 保持开启**。业务源码必须零告警；第三方（Raylib/raygui）不得用 `#pragma` 或降级检查绕过。

---

## 5. 已完成并实测

- 四页（Dashboard / Assistant / Vision / Monitor）+ 顶部状态栏 + 底部导航 + 设置抽屉，1280×600 逻辑布局。
- 窗口化/全屏（F11）、运行中缩放、等比 letterbox。
- 真实系统状态、任务创建/快照/取消、文字消息、Agent 回答、真实 TTS `speak` 动作。
- 语音：开始录音 → 结束录音 → 真实 ASR → 自动消息，UI 无二次提交。
- SSE 实时更新（外部提交的消息在 UI 运行中实时出现，无重复）。
- 真实 640×480 摄像头 JPEG（无数据时占位，不伪造帧）。
- 后端离线不崩溃、显示 OFFLINE/LOST、恢复后自动重连并记事件。
- 干净目录构建成功、`ctest` 3/3 通过、aarch64 ELF、业务零告警。
- 30 秒连续运行无崩溃。

细节与证据见 `docs/TEST_REPORT.md`。

---

## 6. 未完成 / 未验证

| 优先级 | 事项 | 说明 |
| --- | --- | --- |
| P0 | 物理屏 1280×600 目视验收 | 联调后期 `xrandr` 报 `DP-1 disconnected`；此前曾报 1024×600，与任务描述不一致，需现场确认 |
| P0 | 真实触摸点击 | 设备已注册为 `QDtech MPI7003`，但未做物理触摸复验（仅合成 X11 事件验证） |
| P0 | 代码入库与评审 | 分支未 push，未开 PR |
| P1 | 中文输入法 / 软键盘 | 触摸屏无物理键盘时是硬需求；当前输入依赖系统 IME，候选框未测 |
| P1 | 观察证据图 | 验收时没有产生 observation，`GET /evidence/{id}` 分支只走了代码路径 |
| P1 | 异常注入 | ASR 失败、TTS 失败、任务 stop 失败等状态只用代码路径保证，未真机注入 |
| P2 | 真实 GPU 性能与长稳 | 现有数据来自 Xephyr + llvmpipe 软件渲染（RSS≈190MB、CPU≈199%），**不代表** Jetson GL 桌面 |
| P2 | 测试覆盖 | 只有 config/state/text 三组；`api_client`/`event_client` 的解析与重连逻辑尚无单测 |

---

## 7. 已知的坑（按踩坑概率排序）

| # | 现象 | 原因 | 位置 | 处置 |
| --- | --- | --- | --- | --- |
| 1 | 启动后一律 OFFLINE | `config/default.json` 是 8000，现场后端在 8090 | `config/default.json` | 用 `config/jetson.json` 或 `--backend`；运行时会打印 `[robot_ui] UI ready backend=...` |
| 2 | 换机器构建报缺 `Xrandr.h` 等 | 缺 X11 开发头文件，且板端无 sudo | `scripts/build_jetson.sh` | 脚本自动 `apt-get download` + `dpkg-deb -x` 到 `.deps/sysroot`；`.deps/` 不入库，需联网重建 |
| 3 | 首次 `cmake` 卡住/失败 | FetchContent 在线拉 Raylib/raygui/cJSON | `CMakeLists.txt:20-28` | 需要网络；离线环境预置 `build/_deps` 或改成本地 tarball |
| 4 | 升级 Raylib 后突然要求 C++ 编译器 | 构建脚本靠字符串替换 `project(raylib)` → `project(raylib C)` | `CMakeLists.txt:35-41` | 升级版本后确认该字符串仍存在，否则替换失效 |
| 5 | 摄像头永远黑屏 | 二进制 JPEG 响应的 `api_code` 若初值为 -1 会被通用 JSON 成功判断误判 | `src/api_client.c:152` + `parse_json_result()` | 二进制 2xx 业务码必须是 0，JSON 错误仍按 `code` 覆盖 |
| 6 | 后端重启后消息重复/状态回退 | 事件重放 | `src/state.c:255`（`sequence <= last_sequence` 丢弃）、`src/state.c:131-135`（`instance_id` 变化清空） | 改重连逻辑时不要绕过这两处 |
| 7 | SSE 重连丢事件 | 游标格式是 `{instance_id}:{sequence}` | `src/state.c:259`、`src/event_client.c:241-243` | 游标必须随每个事件更新并以 `Last-Event-ID` 发送 |
| 8 | 某些符号显示为 `?` | CJK 字体只预载 ASCII + 中日韩标点 + 0x4E00–0x9FFF + 全角区，`↑/↓` 不在内 | `src/theme.c:40-56` | UI 文案禁用箭头等非子集符号（已改用 `UP/DN`） |
| 9 | 启动慢、显存偏高 | 中文全量字形 24px 预载（约 2 万字） | 同上 | 若要优化，改成按需加载或按实际文案子集化 |
| 10 | 禁用按钮看起来像高亮 | raygui 默认皮肤 | `src/app.c:495-502` | 已显式设置禁用/正常/聚焦/按下四套颜色，勿删 |
| 11 | 小屏上窗口比预期矮、出现黑边 | 启动时若窗口超过显示器，会按 `monitor_height - 56` 收缩（给标题栏留位） | `src/app.c:512-518` | 属预期行为；若要真全屏用 `--fullscreen` 或 F11 |
| 12 | 编译因 raygui 告警失败 | 第三方头文件参与 `-Werror` | `CMakeLists.txt` 的 `SYSTEM PRIVATE` | 保持 `SYSTEM` 包含，禁止给第三方加 `-w` 以外的降级手段 |
| 13 | 自动化点击「没反应」 | 无窗口管理器时 XTest 合成事件不被 GLFW 接收 | 测试脚本层 | 用 `XSendEvent` 直接投递给窗口，或在真实桌面环境验证 |
| 14 | 仓库变大 | `NotoSansSC.ttf` 17MB 直接入库 | `assets/fonts/` | 若团队启用 Git LFS 需一并迁移 |
| 15 | 板端登录口令是共享弱口令 | 历史遗留 | — | 建议交接时轮换，或改用密钥登录 |

---

## 8. 回归清单（改完必跑）

```bash
# 1. 干净构建 + 单测（板端）
cd robot-ui && rm -rf build && ./scripts/build_jetson.sh

# 2. 业务源码零告警
cmake --build build -j"$(nproc)" 2>&1 | grep -E "warning:.*robot-ui/(src|tests)"

# 3. 无 C++ 源文件
find . -path ./build -prune -o -path ./.deps -prune -o \
  -type f \( -name '*.cpp' -o -name '*.cc' -o -name '*.cxx' \) -print

# 4. 后端可用性
curl -s http://127.0.0.1:8090/api/v1/system

# 5. 四页渲染冒烟（会自动退出并落图）
for s in dashboard assistant vision monitor; do
  ./build/robot_ui --backend http://127.0.0.1:8090 --screen "$s" \
    --screenshot "/tmp/ui_$s.png" --screenshot-delay 5
done
```

人工必过项：新建任务 → 输入文字发送 → 听到播报 / ASR 走一遍 → 点停止任务 → 拔掉后端看是否 OFFLINE → 恢复看是否自动重连 → F11 全屏。

---

## 9. 建议的下一步

**P0（不做完不能对外说"可用"）**
1. 推分支 + 开 PR，走负责人评审流程。
2. 接上物理屏，确认最终分辨率（1280×600 还是 1024×600），在真实 GL 桌面上目视四页。
3. 用手指（不是合成事件）点一遍导航、输入框、发送、语音、停止。

**P1（可用性缺口）**
4. 触摸场景的中文输入：评估系统 IME 候选框是否可用，不可用则做屏内拼音/软键盘；这是触摸屏无键盘时的阻断问题。
5. 造一次带 observation 的任务，验证 `GET /evidence/{id}` 的真实证据图渲染与 STALE 逻辑。
6. 注入失败路径：ASR 失败、TTS 失败、stop 失败，确认状态机落 `SPEECH_FAILED` 且提示可读。

**P2（工程完善）**
7. 给 `api_client` / `event_client` 补单测（响应解析、队列上限、重连退避），目前这两块只能靠联调验证。
8. 真实 GPU 占用与 2 小时长稳、温升采样，替换掉 Xephyr 的失真数据。
9. 设置抽屉扩展（音量/亮度/后端地址可改），以及消息分页加载（当前上限 100 条）。

---

## 10. 需要负责人先拍板

1. **交付方式**：推 `feature/robot-native-ui` 到 origin 开 PR，还是打包 patch/bundle 给外部同学（涉及 17MB 字体的传递）。
2. **是否纳入 CI**：目前 C 工程没有 CI；若纳入，需要一台带 X11 头的构建机或容器镜像。
3. **硬件事实**：物理屏最终是 1280×600 还是 1024×600；板端弱口令是否轮换。这两条会直接影响验收结论怎么写。

---

## 11. 文档索引

- `README.md`：依赖安装、构建、运行、配置项、字体、常见问题排查。
- `docs/INTERFACE_AUDIT.md`：后端能力与接口审计（含"后端没提供、因此 UI 不实现"的清单）。
- `docs/TEST_REPORT.md`：构建、CTest、真实后端联调、显示交互、性能与未验证项的原始记录。
- `AGENTS.md` / `docs/CODING_SPEC.md`（仓库根）：Python 侧的开发规范，C 侧不强制但代码风格与日志格式沿用了同一套约定。
