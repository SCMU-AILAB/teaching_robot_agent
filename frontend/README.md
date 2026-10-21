# 课堂操作终端（Flutter）

主用形态是 **macOS 桌面 App**：装在 Mac 上运行，通过公网 IP（或内网地址）连接
Jetson 算力板的设备端服务，推理与语音合成仍在服务器侧。Web 构建保留给远程看板
和板端本机调试。

页面只接设备端真实数据：任务、对话、观察、录音/播报状态来自 `GET /api/v1/...`
与 SSE 事件流，观察视野是 Jetson 摄像头的画面。没有本地示例对话、示例场景图片，
也没有模拟数据源兜底——连不上设备时显示「未连接服务器」。

## 运行与检查

```bash
cd frontend
flutter pub get
flutter analyze
flutter test                       # 66 项
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

### 内网直连（当前调试形态）

Mac 与 Jetson 在同一局域网时，地址填 `http://192.168.0.127:8090`；板端 avahi
在跑，`http://qwq.local:8090` 同样可用（换 IP 后不用改配置）。实测：

| 接口 | 耗时 | 说明 |
|---|---|---|
| `GET /system` | 13–15 ms | 连接指示与摄像头状态 |
| `GET /tasks/{id}/session` | 14 ms | 聚合快照 |
| `GET /camera/snapshot?max_width=640` | 19 ms / 56 KB | 2 fps 约 0.9 Mbps |

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

设备端 `spike/device_api.py` 是联调服务，**没有鉴权、`Access-Control-Allow-Origin: *`**，
能建/停任务、读对话、拉摄像头画面。所以：

- 优先用隧道组网（WireGuard / Tailscale / SSH 端口转发），不要直接映射 8090；
- 必须公网直连时：只放行固定来源 IP、改非默认端口、外加 TLS 反向代理；
- 要真正做到可公开访问，得先给 API 加 token（合同 + `device_api` + 前端一起改）。

画面带宽：桌面端每秒拉 `camera/snapshot` 若干次，当前默认 **3 fps / 800px 宽**
（约 1–2 Mbps）。觉得模糊可以把 `classroom_page.dart` 里的 `maxWidth` 调回 960，
觉得卡就降到 2 fps——这两个数按你实际公网上行带宽定。

## Web 构建（远程看板/板端调试）

```bash
flutter build web --dart-define=DEVICE_API_BASE=http://<设备IP>:8090
# 把 build/web 交给任意静态服务器；不想写死地址就用页面参数 ?api=...
```

Web 端把 `GET /api/v1/camera/stream`（MJPEG）交给浏览器 `<img>` 原生解码；
桌面端无法解码 multipart，改为按帧轮询 `camera/snapshot`（这就是帧率的来源）。

## 已实现

- 连接三态：正在连接设备… / 设备连接正常 / 未连接服务器；服务器通但摄像头没
  画面时单独显示「摄像头未就绪」，不误报成未连接。
- 创建课堂目标、文字提问、语音录音入口；对话按 SSE 增量追加并去重。
- 观察结果、教学阶段、动作记录、合成与播放状态分开展示，字段全部来自快照。
- 停止请求先进入 cancelling，收到设备确认后才进入 cancelled。
- 断线提示、手动重连、`stream.reset` 触发快照重放、配置持久化。
- 桌面与手机布局；页面销毁时关闭订阅、定时器与 HTTP 连接。

## 分层与边界

`features/classroom/` 存放页面、控制器、模型与数据源适配器；`widgets/` 只负责
展示。页面不发网络请求，统一走 `ClassroomService`。

`DeviceClassroomService` 是合同 `API_CONTRACT.md` v0.2 草案的前端实现：聚合快照
+ SSE（`Last-Event-ID` 断点续传）+ 命令通道，统一响应 `{code,message,data}`，
非 0 code 包装为 `ClassroomFailure`。合同仍为草案，端点或字段变更须先改合同。

`MockClassroomService` 只作为测试替身存在（`test/` 使用），页面不再引用。

摄像头是只读资源，页面不提供任何设备控制入口；停止按钮只发任务的 `cancelling`。

## 素材

- `assets/NotoSansSC.ttf`：Google Fonts Noto Sans SC，SIL Open Font License，
  许可见 `assets/OFL.txt`。

## 尚未完成

- 录音与播报仍是设备端状态展示，真实采音与 TTS 播放由语音层（`speech/`）落地后联调。
- 教学回答在设备端仍是模拟内容（响应里标记 `simulated`），待 Teaching Agent 接入。
- 公网链路没有 TLS 与鉴权，见上文「公网访问的前提与风险」。
- Web 平台视图是真实 DOM 节点，Flutter 控件无法叠在其上，画面角标放在标题行。
