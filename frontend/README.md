# 课堂操作终端（Flutter）

主用形态是 **macOS 桌面 App**：装在 Mac 上运行，通过公网 IP（或内网地址）连接
Jetson 算力板的设备端服务，推理与语音合成仍在服务器侧。Web 构建保留给远程看板
和板端本机调试。

页面只接机器人后端的真实数据，接口以根目录 `API_CONTRACT.md` v1.0 为准：任务、
对话、动作、观察、录音来自 `GET /api/v1/tasks/{id}` 快照与 SSE 事件流，播报状态
取自最近一个 `speak` 动作，观察视野是 Jetson 摄像头的画面。没有本地示例对话、示例场景图片，
也没有模拟数据源兜底——连不上设备时显示「未连接服务器」。

## 运行与检查

```bash
cd frontend
flutter pub get
flutter analyze
flutter test                       # 85 项
```

后端（Jetson）按前端默认端口启动，局域网访问需显式监听所有地址：

```bash
ROBOT_API_HOST=0.0.0.0 ROBOT_API_PORT=8090 uv run python -m app.robot_server
```

## 设备地址：运行时设置，不用重新构建

Mac App 右上角有「设备地址」按钮（`dns` 图标），填 `http://<公网IP>:8090`，
可先「测试连接」确认能拿到实例名与摄像头状态，保存后立即重连并写入本地：

- macOS：`~/Library/Application Support/TeachingRobot/config.json`
- Web：`localStorage["teaching_robot.device_api_base"]`

解析优先级（`lib/core/config/device_endpoint.dart`）：

1. 设置里保存的地址 ← 公网部署用这一档
2. 页面查询参数 `?api=http://host:port`（Web 调试）
3. 构建期 `--dart-define=DEVICE_API_BASE=...`
4. 页面同源（页面由设备端托管时命中）
5. 本机 `http://127.0.0.1:8090`

### 启动自动发现（换网络不用改配置）

App 启动顺序：读上次保存的地址 → 先连一次（1.5 s 超时）→ 连不上才扫局域网：

1. mDNS 名字 `qwq.local` / `jetson.local` / `orin.local`（命中只需几十毫秒）；
2. 逐个扫本机各 `/24` 网段的 `8090`，每批 64 个并发，一个网段约 1–2 秒；
3. 用 `GET /api/v1/system` 校验 `code` + `data.instance_id` + `data.camera`，
   多个候选优先 `instance_id` 以 `jetson` 开头的那台；
4. 命中即写回配置文件并自动连上；头部状态依次是
   「正在查找设备…」→「设备连接正常」。

失败不假装成功：扫不到就停在「未连接服务器」，点右上角刷新可重新扫描。
浏览器开不了裸 TCP 与 mDNS，Web 版只保留单地址校验（手填或用 `?api=`）。

命令行版（含服务器连通性检查）在 `tooling/find-jetson.sh`：

```bash
./tooling/find-jetson.sh                  # 发现 + 校验 + 写配置
./tooling/find-jetson.sh --restart        # 顺手重启 App
./tooling/find-jetson.sh --server 1.2.3.4:22   # 顺带测服务器 SSH 端口
```

服务器地址不参与扫描（不在同一网段、也不该被扫），写在
`~/.config/teachingrobot/server.json`，脚本每次跑完会报告可达性。

### 内网直连（当前调试形态）

Mac 与 Jetson 在同一局域网时，地址填 `http://192.168.0.127:8090`；板端 avahi
在跑，`http://qwq.local:8090` 同样可用（换 IP 后不用改配置）。实测：

| 接口 | 耗时 | 说明 |
|---|---|---|
| `GET /system` | 13–15 ms | 连接指示与摄像头状态 |
| `GET /tasks/{id}` | 14 ms | 任务快照 |
| `GET /camera/snapshot?max_width=640` | 19 ms / 56 KB | 2 fps 约 0.9 Mbps |

以上数据测于 10 月的板端联调服务，迁到 v1.0 后端后待复测。

## macOS 桌面版与 DMG

```bash
# 桌面版不需要写死地址，装好后在应用内设置即可
flutter build macos --release

# 打成 DMG：应用 + Applications 快捷方式，拖拽即安装
mkdir -p /tmp/dmg-stage
cp -R build/macos/Build/Products/Release/TeachingRobot.app /tmp/dmg-stage/
ln -s /Applications /tmp/dmg-stage/Applications
hdiutil create -volname "教学机器人" -srcfolder /tmp/dmg-stage \
  -ov -format UDZO TeachingRobot-<版本>-macos.dmg
```

- 应用名 `TeachingRobot`，Finder 显示名「教学机器人」，Bundle ID
  `com.scmu.ailab.teachingrobot`；已开沙箱网络出口权限
  （`com.apple.security.network.client`），否则 HTTP/SSE/画面全部被拒。
- 签名 ad-hoc、未公证：下载后首次打开若被 Gatekeeper 拦，右键「打开」，或
  `xattr -dr com.apple.quarantine /Applications/TeachingRobot.app`。
- 本工程未使用 Swift Package Manager（无 Swift 插件依赖）。若以后引入 macOS
  原生插件，需要 `flutter config --enable-swift-package-manager` 或改用 CocoaPods。

## 公网访问的前提与风险

后端 `app.robot_server` **没有鉴权**，能建/停任务、读对话、拉摄像头画面。所以：

- 优先用隧道组网（WireGuard / Tailscale / SSH 端口转发），不要直接映射 8090；
- 必须公网直连时：只放行固定来源 IP、改非默认端口、外加 TLS 反向代理；
- 要真正做到可公开访问，得先给 API 加 token（合同 + 后端 + 前端一起改）。

画面带宽：桌面端每秒拉 `camera/snapshot` 若干次，当前默认 **3 fps / 800px 宽**
（约 1–2 Mbps）。觉得模糊可以把 `classroom_page.dart` 里的 `maxWidth` 调回 960，
觉得卡就降到 2 fps——这两个数按你实际公网上行带宽定。

## Web 构建（远程看板/板端调试）

```bash
flutter build web --dart-define=DEVICE_API_BASE=http://<设备IP>:8090
# 把 build/web 交给任意静态服务器；不想写死地址就用页面参数 ?api=...
```

v1.0 没有 MJPEG 流，各平台都按帧轮询 `camera/snapshot`（这就是帧率的来源）。
后端没有跨源响应头，Web 版页面须与后端同源托管；桌面版不受此限制。

## 已实现

- 连接三态：正在连接设备… / 设备连接正常 / 未连接服务器；服务器通但摄像头没
  画面时单独显示「摄像头未就绪」，不误报成未连接。
- 创建任务、文字提问、语音录音入口；对话按 SSE 增量追加并去重，机器人回答先以
  播报文本出现、随后补全，不重复追加；被拒绝的消息显示机器人说明与原因。
- 观察结果、动作记录、播报状态分开展示，字段全部来自快照与事件。
- 停止请求先进入 cancelling，收到设备确认后才进入 cancelled。
- 断线提示、手动重连、`stream.reset` 触发快照重放、配置持久化。
- 桌面与手机布局；页面销毁时关闭订阅、定时器与 HTTP 连接。

## 分层与边界

`features/classroom/` 存放页面、控制器、模型与数据源适配器；`widgets/` 只负责
展示。页面不发网络请求，统一走 `ClassroomService`。

`DeviceClassroomService` 是 `API_CONTRACT.md` v1.0 的前端实现：任务快照 + SSE
（`Last-Event-ID` 断点续传，`stream.reset` 后重读快照，连续重置按断线间隔退避）
+ 任务/消息/录音命令通道，统一响应 `{code,message,data}`，非 0 code 包装为
`ClassroomFailure`。v1.0 没有课堂阶段、观察请求和 MJPEG：阶段由消息处理状态推导，
“刷新观察”以一句提问请机器人重新观察。

`core/constants/api_paths.dart` 按根目录 API_CONTRACT.md v1.0 定义全部网络路径，前端只能使用其中的端点；新增或修改路径必须先改接口文档并经项目负责人确认。只由数据源适配器连接事件接口，禁止新增 Runtime.next_event() 消费者。

`MockClassroomService` 只作为测试替身存在（`test/` 使用），页面不再引用。

摄像头是只读资源，页面不提供任何设备控制入口；停止按钮只发任务的 `cancelling`。

## 素材

- `assets/NotoSansSC.ttf`：Google Fonts Noto Sans SC，SIL Open Font License，
  许可见 `assets/OFL.txt`。

## 尚未完成

- 尚未与 Jetson 上的 v1.0 后端实机联调；本地已用模拟设备后端走通创建、提问、事件流与停止。
- 页面没有“确认完成”（`PATCH status=completed`）入口，任务只能停止。
- 公网链路没有 TLS 与鉴权，见上文「公网访问的前提与风险」。
- Web 平台视图是真实 DOM 节点，Flutter 控件无法叠在其上，画面角标放在标题行。
