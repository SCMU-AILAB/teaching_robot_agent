# P3 课堂展示：Flutter 模拟版本

本阶段只完成页面模拟版本。数据源为进程内 `MockClassroomService`，不调用 Python 语音服务、不访问麦克风、不实际播音，也没有 HTTP/SSE 后端。页面持续标记“模拟课堂”，场景图片为项目自绘的示例桌面。

## 运行与检查

```bash
cd frontend
flutter pub get
flutter analyze
flutter test
flutter run -d web-server --web-hostname 127.0.0.1 --web-port 5174
```

已验证 Flutter 3.38.2 / Dart 3.10.0；最低 Dart 版本为 3.10。依赖由 pubspec.yaml 与 pubspec.lock 管理。升级 Flutter 时需重新解析依赖并执行分析、测试与构建。

## 已实现

- 创建课堂目标、文字提问、回答文本显示。
- 示例场景图片、观察结果、刷新观察。
- 教学阶段、动作记录、合成和播放状态分开展示。
- 开始录音、主动结束、15 秒自动结束、录音计时。
- 停止请求先进入 cancelling，收到模拟确认后才进入 cancelled；取消清除后续模拟事件。
- 本地事件快照刷新、重复与乱序事件去重、断线提示、重连获取完整快照。
- 请求失败、无有效语音、识别失败和合成失败场景。
- 桌面与手机布局；页面销毁时关闭订阅、定时器与模拟数据源。

右上角“演示场景”菜单切换失败场景。连接图标可模拟断线，再通过“重新连接”恢复。请求失败场景切回“正常交互”后可以重试。

## 分层与后续对接

`features/classroom/` 存放页面、控制器、模型和内部数据源接口；widgets/ 只负责展示。页面不发网络请求。`ClassroomService` 是本地页面数据源边界，不是新增后端接口合同。

`core/constants/api_paths.dart` 当前不定义网络端点。API_CONTRACT.md 和 MODULE_CONTRACT.md 均仍为草案；真实 API/SSE 适配必须等核心提供已审核的端点、快照和广播能力。以后只由数据源适配器连接核心事件接口，禁止新增 Runtime.next_event() 消费者。

本地快照的 sequence 用于模拟去重，不能宣称已验证后端事件游标、SSE 重放或网络恢复协议。页面的停止按钮只记录模拟 stop_task 命令，尚不能停止真实设备。教学反馈文本是固定示例，不替代 Teaching Agent。

音频互斥的真实 Python 实现归语音层 AudioDeviceLease，由应用装配方注入共享实例；页面禁用按钮只是交互约束，不替代服务端设备互斥。参见 MODULE_CONTRACT 的待审核归属补充。

## 素材

- `assets/scene.png`：项目自绘模拟场景，无真实摄像头信息。
- `assets/NotoSansSC.ttf`：Google Fonts Noto Sans SC，SIL Open Font License，许可见 `assets/OFL.txt`。字体随应用本地加载。

## 尚未完成

真实摄像头/语音设备与模型服务、页面到 Python 服务的网络集成、核心教学决策、任务级统一资源取消、HTTP/SSE、真实断网集成验收及学生完整交互验收均未完成。此版本可用于审查页面流程和内部状态处理。

2026-10-07 合并前复核：首次观察期间禁止启动新输入；同实例的迟到快照不能覆盖新事件；重连须成功恢复快照后才显示连接正常。新增对应回归测试，Flutter 分析无诊断，18 项测试通过。页面仍是可选课堂模拟，不替代 Python 家庭机器人主链路。
