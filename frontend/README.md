# 课堂操作终端（Flutter，接 Jetson 设备端）

页面只接设备端真实数据：任务、对话、观察、录音/播报状态来自
`GET /api/v1/...` 与 SSE 事件流，观察视野是 Jetson 摄像头的实时画面。
没有本地示例对话、示例场景图片，也没有模拟数据源兜底——连不上设备时
整块显示「未连接服务器」。

## 运行与检查

```bash
cd frontend
flutter pub get
flutter analyze
flutter test                       # 55 项
```

页面由设备端服务同源托管时（推荐，端口 8090）直接构建即可：

```bash
flutter build web
# 把 build/web 交给设备端服务托管
```

开发机与设备分离时显式注入设备地址（地址不写进源码）：

```bash
flutter run -d chrome --dart-define=DEVICE_API_BASE=http://<设备IP>:8090
flutter build web --dart-define=DEVICE_API_BASE=http://<设备IP>:8090
```

地址解析优先级见 `lib/core/config/device_endpoint.dart`：查询参数 `?api=`
覆盖 → 构建期注入 → 页面同源 → 本机 `127.0.0.1:8090`。

## macOS 桌面版与 DMG

```bash
# 桌面版同样在构建期注入设备地址（无 URL 参数可用）
flutter build macos --release --dart-define=DEVICE_API_BASE=http://<设备IP>:8090

# 打成 DMG：应用 + Applications 快捷方式，拖拽即安装
mkdir -p /tmp/dmg-stage
cp -R build/macos/Build/Products/Release/TeachingRobot.app /tmp/dmg-stage/
ln -s /Applications /tmp/dmg-stage/Applications
hdiutil create -volname "教学机器人" -srcfolder /tmp/dmg-stage \
  -ov -format UDZO TeachingRobot-<版本>-macos.dmg
```

- 应用名 `TeachingRobot`，Finder 显示名「教学机器人」，Bundle ID
  `com.scmu.ailab.teachingrobot`；已开沙箱网络出口权限
  （`com.apple.security.network.client`），否则应用内 HTTP/SSE/MJPEG 全部被拒。
- 签名是 ad-hoc（无开发者证书、未公证）。从网络下载后首次打开若被 Gatekeeper
  拦住，右键「打开」，或 `xattr -dr com.apple.quarantine /Applications/TeachingRobot.app`。
- 本工程未使用 Swift Package Manager（没有 Swift 插件依赖，Xcode 工程里的
  `FlutterGeneratedPluginSwiftPackage` 引用已移除）。若以后引入 macOS 原生插件，
  需要 `flutter config --enable-swift-package-manager` 或改用 CocoaPods。


已验证 Flutter 3.47.4 / Dart 3.10；最低 Dart 版本为 3.10。依赖由
`pubspec.yaml` 与 `pubspec.lock` 管理。

## 已实现

- 观察视野即设备摄像头：Web 端把 `GET /api/v1/camera/stream`（MJPEG）
  交给浏览器 `<img>` 原生解码；其他平台轮询 `camera/snapshot` 换帧。
  首帧未到达、断流或未连接服务器时显示未连接占位（断线云图标）。
- 创建课堂目标、文字提问、语音录音入口；对话按 SSE 事件增量追加并去重。
- 观察结果、教学阶段、动作记录、合成与播放状态分开展示，字段全部来自快照。
- 停止请求先进入 cancelling，收到设备确认后才进入 cancelled。
- 断线提示、重连重取聚合快照、`stream.reset` 触发快照重放。
- 桌面与手机布局；页面销毁时关闭订阅、定时器与 HTTP 连接。

## 分层与边界

`features/classroom/` 存放页面、控制器、模型与数据源适配器；`widgets/`
只负责展示。页面不发网络请求，统一走 `ClassroomService`。

`DeviceClassroomService` 是合同 `API_CONTRACT.md` v0.2 草案的前端实现：
聚合快照 + SSE（`Last-Event-ID` 断点续传）+ 命令通道，统一响应
`{code,message,data}`，非 0 code 包装为 `ClassroomFailure`。合同仍为草案，
端点或字段变更须先改合同再改适配器。

`MockClassroomService` 只作为测试替身存在（`test/` 使用），页面不再引用，
避免把模拟结果当真实能力。

连接语义：断线由连接流即时下发；「已连上」以命令/快照成功为准（首屏
快照、重连、SSE 建流三种确认），因此快照失败时不会误报连接正常。

摄像头是只读资源，页面不提供任何设备控制入口；停止按钮只发任务的
`cancelling`，不直接操作机械或音频硬件。

## 素材

- `assets/NotoSansSC.ttf`：Google Fonts Noto Sans SC，SIL Open Font
  License，许可见 `assets/OFL.txt`。

## 尚未完成

- 录音与播报仍是设备端状态展示，真实采音与 TTS 播放由语音层（`speech/`）
  落地后再联调。
- 教学回答在设备端仍是模拟内容（响应里标记 `simulated`），待 Teaching
  Agent 接入后替换。
- Web 平台视图是真实 DOM 节点，Flutter 控件无法叠在其上，因此画面状态
  角标放在面板标题行而不是画面内；如需画面内叠加，要改为 DOM 合成或
  退回单帧轮询。
