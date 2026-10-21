// frontend/lib/features/classroom/classroom_page.dart
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

import '../../core/config/device_discovery.dart';
import '../../core/config/device_endpoint.dart';
import '../../core/config/endpoint_store.dart';
import '../../core/constants/app_radius.dart';
import 'classroom_controller.dart';
import 'device_classroom_service.dart';
import 'session_model.dart';
import 'widgets/activity_panel.dart';
import 'widgets/conversation_panel.dart';
import 'widgets/endpoint_dialog.dart';
import 'widgets/field_error_box.dart';
import 'widgets/observation_panel.dart';
import 'widgets/record_toggle_button.dart';
import 'widgets/rolling_text.dart';
import 'widgets/send_button.dart';
import 'widgets/status_label.dart';
import 'widgets/task_toggle_button.dart';

/// 数据源装配：页面只接 Jetson 设备端真实数据，不再有模拟数据源。
///
/// 服务地址在运行时解析（[resolveDeviceBaseUrl]）：设置里保存的地址优先，
/// 其次是构建期注入、页面同源、本机端口。Mac 端通过公网 IP 连 Jetson 时，
/// 在页面右上角的「设备地址」里填写即可，不需要重新构建。
class ClassroomPage extends StatefulWidget {
  const ClassroomPage({this.controller, super.key});
  final ClassroomController? controller;

  @override
  State<ClassroomPage> createState() => _ClassroomPageState();
}

class _ClassroomPageState extends State<ClassroomPage> {
  late ClassroomController _controller;

  /// 只在页面自建控制器时可换地址；测试注入的控制器不参与重建。
  bool _ownsController = false;

  /// 当前生效的设备地址，供设置对话框回显与比较。
  String _baseUrl = '';

  /// 正在扫描局域网：连接徽标与观察区都按「连接中」处理，不提前报红。
  bool _discovering = false;

  final TextEditingController _target = TextEditingController();
  final TextEditingController _message = TextEditingController();
  final GlobalKey _messageInputKey = GlobalKey();

  /// 发送形变进行中：隐藏真实输入框，让「整框变小变蓝」可见。
  final ValueNotifier<bool> _sendMorphing = ValueNotifier<bool>(false);

  @override
  void initState() {
    super.initState();
    if (widget.controller != null) {
      _controller = widget.controller!;
      unawaited(_controller.initialize());
    } else {
      _ownsController = true;
      _baseUrl = resolveDeviceBaseUrl();
      _controller = _buildController(_baseUrl);
      unawaited(_bootstrap());
    }
  }

  /// 观察区/徽标共用的连接视图：扫描期间按「连接中」展示，避免闪一下红色。
  DeviceConnection get _connectionView =>
      _discovering ? DeviceConnection.connecting : _controller.connection;

  ClassroomController _buildController(String baseUrl) =>
      ClassroomController(DeviceClassroomService(baseUrl: baseUrl));

  /// 启动流程：读上次地址 → 连一次 → 连不上就扫局域网。
  ///
  /// 换内网后 IP 变了也不用改任何配置：首屏连不上即自动发现并写回配置。
  Future<void> _bootstrap() async {
    final String? saved = await EndpointStore.load();
    if (!mounted) return;
    String base = _baseUrl;
    bool switched = false;
    if (saved != null) {
      final String resolved = resolveDeviceBaseUrl(saved: saved);
      if (resolved != base) {
        base = resolved;
        _switchEndpoint(resolved); // 内部会拉首屏快照
        switched = true;
      }
    }
    if (!switched) unawaited(_controller.initialize());
    unawaited(_autoDiscover(base));
  }

  /// 首屏连不上时才扫描，避免每次启动都白等几秒。
  Future<void> _autoDiscover(String base) async {
    if (!mounted || !_ownsController) return;
    final DeviceProbe? probe = await probeDevice(
      base,
      timeout: const Duration(milliseconds: 1500),
    );
    if (!mounted || probe != null) return; // 已连上，不扫
    await _discoverDevice();
  }

  /// 扫局域网写回配置；失败保持未连接，由控制器给出提示。
  Future<void> _discoverDevice() async {
    if (_discovering || !mounted) return;
    setState(() => _discovering = true);
    DeviceProbe? found;
    try {
      found = await discoverDevice();
    } finally {
      if (mounted) setState(() => _discovering = false);
    }
    if (!mounted) return;
    if (found == null) {
      // 没找到：让控制器给出「未连接服务器」，不假装连上。
      unawaited(_controller.reconnect());
      return;
    }
    if (found.baseUrl == _baseUrl) {
      unawaited(_controller.reconnect());
      return;
    }
    await EndpointStore.save(found.baseUrl);
    if (!mounted) return;
    _switchEndpoint(found.baseUrl);
  }

  /// 换地址：新建控制器并让它自己拉快照，旧控制器延后回收。
  void _switchEndpoint(String baseUrl) {
    final ClassroomController previous = _controller;
    final ClassroomController next = _buildController(baseUrl);
    setState(() {
      _baseUrl = baseUrl;
      _controller = next;
    });
    unawaited(next.initialize());
    // 等这一帧重建完再释放旧控制器，避免 AnimatedBuilder 还挂在已销毁的
    // ChangeNotifier 上。
    WidgetsBinding.instance.addPostFrameCallback((_) => previous.dispose());
  }

  Future<void> _openEndpointSettings() async {
    final String? address = await showDialog<String>(
      context: context,
      builder: (BuildContext context) => EndpointDialog(current: _baseUrl),
    );
    if (address == null || !mounted || address == _baseUrl) return;
    await EndpointStore.save(address);
    if (!mounted) return;
    _switchEndpoint(address);
  }

  @override
  void dispose() {
    _sendMorphing.dispose();
    _target.dispose();
    _message.dispose();
    if (_ownsController) _controller.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final bool accepted = await _controller.submitText(_message.text);
    if (accepted && mounted) _message.clear();
  }

  /// 按错误来源取文案；不属于 [source] 时返回空，小框平滑收起。
  String _errorFor(String source) =>
      _controller.errorSource == source ? (_controller.error ?? '') : '';

  /// 控件旁的小错误框：控制器标注来源，空串等同无错误。
  Widget _inlineError(String source, {Widget? action}) =>
      FieldErrorBox(message: _errorFor(source), action: action);

  Widget _header() => Wrap(
    alignment: WrapAlignment.spaceBetween,
    crossAxisAlignment: WrapCrossAlignment.center,
    spacing: 24,
    runSpacing: 12,
    children: [
      Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(
            Symbols.smart_toy_rounded,
            size: 28,
            color: Color(0xff147d64),
            fill: 1,
          ),
          const SizedBox(width: 12),
          const Text(
            '教学机器人',
            style: TextStyle(fontWeight: FontWeight.w700, fontSize: 21),
          ),
          const SizedBox(width: 12),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: ShapeDecoration(
              shape: AppRadius.containerShape(radius: AppRadius.chip),
              color: const Color(0xffe6eee9),
            ),
            child: Text(
              // 任务编号来自设备端快照，未开始时只标明数据来源。
              _controller.state.taskId > 0
                  ? '课堂 #${_controller.state.taskId}'
                  : '设备课堂',
              style: const TextStyle(fontSize: 11, color: Color(0xff147d64)),
            ),
          ),
        ],
      ),
      Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Symbols.circle_rounded,
            size: 7,
            color: switch (_connectionView) {
              DeviceConnection.connecting => const Color(0xff94a3a0),
              DeviceConnection.connected => const Color(0xff147d64),
              DeviceConnection.disconnected => const Color(0xffb6403a),
            },
          ),
          const SizedBox(width: 7),
          Text(
            _discovering
                ? '正在查找设备…'
                : switch (_controller.connection) {
                    DeviceConnection.connecting => '正在连接设备…',
                    DeviceConnection.connected => '设备连接正常',
                    DeviceConnection.disconnected => '未连接服务器',
                  },
            style: const TextStyle(fontSize: 12),
          ),
          const SizedBox(width: 8),
          if (_ownsController)
            IconButton(
              tooltip: '设备地址 · $_baseUrl',
              onPressed: () => unawaited(_openEndpointSettings()),
              icon: const Icon(Symbols.dns_rounded, size: 20),
            ),
          IconButton(
            tooltip: _controller.connected ? '刷新设备状态' : '重新扫描设备',
            onPressed: _controller.busy || _discovering
                ? null
                : () => unawaited(
                    _controller.connected
                        ? _controller.reconnect()
                        : _discoverDevice(),
                  ),
            icon: Icon(
              _controller.connected
                  ? Symbols.sync_rounded
                  : Symbols.refresh_rounded,
              size: 20,
            ),
          ),
        ],
      ),
    ],
  );

  /// 连接级错误的小框：贴在头部连接状态下方，附「重新连接」操作。
  Widget _connectionError() => _inlineError(
    'connection',
    action: TextButton(
      onPressed: () => unawaited(_controller.reconnect()),
      child: const Text('重新连接'),
    ),
  );

  Widget _taskControls(bool compact) {
    final SessionModel state = _controller.state;
    final Widget target = TextField(
      controller: _target,
      key: const Key('task-target'),
      enabled: !state.active && _controller.connected,
      maxLength: 2000,
      decoration: const InputDecoration(
        labelText: '课堂目标',
        hintText: '输入本次课堂要完成的目标',
        counterText: '',
      ),
      onSubmitted: (_) => unawaited(_controller.createTask(_target.text)),
    );
    final Widget button = TaskToggleButton(
      active: state.active,
      cancelling: state.taskStatus == 'cancelling',
      onStart: state.active || _controller.busy || !_controller.connected
          ? null
          : () => unawaited(_controller.createTask(_target.text)),
      onStop: state.active && state.taskStatus != 'cancelling'
          ? () => unawaited(_controller.stop())
          : null,
    );
    // 课堂目标行错误框贴在目标输入下方，宽度跟随输入列。
    final Widget errorBox = _inlineError('task');
    return compact
        ? Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [target, errorBox, const SizedBox(height: 12), button],
          )
        : Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(child: target),
                  const SizedBox(width: 16),
                  button,
                ],
              ),
              errorBox,
            ],
          );
  }

  Widget _composer() {
    final SessionModel state = _controller.state;
    final bool recording = state.recording == 'recording';
    // 只拦真正的音频占用：播报中、转写中。观察是否已有结果不构成录音前提，
    // 设备端的观察是按次请求（POST observations），教学任务开始即可说话。
    final bool audioBusy =
        ['playing', 'synthesizing'].contains(state.playback) ||
        state.recording == 'transcribing';
    final bool available =
        state.taskStatus == 'running' &&
        _controller.connected &&
        !_controller.busy;
    // 发送加载态：点击瞬间起转，老师回答事件到达即停。
    // 纯事件驱动（awaitingReply 由回答消息收口），接入真实推理后无需改动。
    final bool sending = _controller.awaitingReply;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: 20),
        Row(
          children: [
            Expanded(
              child: ValueListenableBuilder<bool>(
                valueListenable: _sendMorphing,
                // 形变期整框透明（保布局不跳动），幽灵接管视觉。
                builder: (BuildContext context, bool morphing, Widget? child) =>
                    Opacity(opacity: morphing ? 0 : 1, child: child),
                child: KeyedSubtree(
                key: _messageInputKey,
                child: TextField(
                  key: const Key('message-input'),
                  controller: _message,
                  // 播报期间也可打字，只有任务不可用才禁用。
                  enabled: available,
                  minLines: 1,
                  maxLines: 4,
                  maxLength: 4000,
                  decoration: const InputDecoration(
                    hintText: '输入你的发现或问题…',
                    counterText: '',
                  ),
                  onSubmitted: (_) => unawaited(_send()),
                ),
                ),
              ),
            ),
            const SizedBox(width: 8),
            SendButton(
              loading: sending,
              onPressed: available && !recording && !sending
                  ? () => unawaited(_send())
                  : null,
            ),
          ],
        ),
        // 消息输入行的错误框贴在输入框下方；发送相关的兜底错误也挂在这里。
        _inlineError('message'),
        _inlineError('send'),
        const SizedBox(height: 12),
        Wrap(
          spacing: 16,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            RecordToggleButton(
              recording: recording,
              onPressed: available && !audioBusy
                  ? () => unawaited(_controller.record())
                  : null,
            ),
            StatusLabel(state.recording),
            if (recording)
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  // 逐秒数字滚轮：只有变化的数字卷动，「/ 15 秒」静止。
                  RollingText(
                    state.recordingSeconds.toString().padLeft(2, '0'),
                    key: const Key('record-timer'),
                    style: const TextStyle(
                      fontSize: 12,
                      color: Color(0xffb6403a),
                    ),
                  ),
                  const Text(
                    ' / 15 秒',
                    style: TextStyle(fontSize: 12, color: Color(0xffb6403a)),
                  ),
                ],
              ),
          ],
        ),
        // 录音相关错误贴近录音控件，不占用页面顶部。
        _inlineError('record'),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: _controller,
    builder: (BuildContext context, Widget? child) => Scaffold(
      body: SafeArea(
        child: LayoutBuilder(
          builder: (BuildContext context, BoxConstraints constraints) {
            final bool compact = constraints.maxWidth < 900;
            final SessionModel state = _controller.state;
            final Widget conversation = Column(
              children: [
                ConversationPanel(
                  state: state,
                  messageInputKey: _messageInputKey,
                  sendMorphing: _sendMorphing,
                ),
                _composer(),
              ],
            );
            final Widget observation = ObservationPanel(
              state: state,
              connection: _connectionView,
              streamUrl: _controller.cameraStreamUrl(fps: 2),
              snapshotUrl: _controller.cameraSnapshotUrl(maxWidth: 640),
              onRefresh: state.taskStatus == 'running' && _controller.connected
                  ? () => unawaited(_controller.refresh())
                  : null,
            );
            return SingleChildScrollView(
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 1320),
                  child: Padding(
                    padding: EdgeInsets.all(compact ? 18 : 32),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _header(),
                        // 连接错误贴在连接状态下方，不再整页顶部横幅推挤布局。
                        _connectionError(),
                        const Divider(height: 40),
                        Wrap(
                          spacing: 20,
                          runSpacing: 8,
                          crossAxisAlignment: WrapCrossAlignment.center,
                          children: [
                            const Text(
                              '一起观察，一起学习',
                              style: TextStyle(
                                fontSize: 26,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            StatusLabel(state.taskStatus),
                          ],
                        ),
                        const SizedBox(height: 20),
                        _taskControls(compact),
                        const SizedBox(height: 22),
                        Container(
                          decoration: ShapeDecoration(
                            shape: AppRadius.containerShape(
                              radius: AppRadius.card,
                            ),
                            color: Colors.white,
                          ),
                          padding: EdgeInsets.all(compact ? 16 : 24),
                          child: compact
                              ? Column(
                                  children: [
                                    observation,
                                    const Divider(height: 40),
                                    conversation,
                                  ],
                                )
                              : Row(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Expanded(flex: 6, child: conversation),
                                    const SizedBox(width: 36),
                                    Expanded(flex: 5, child: observation),
                                  ],
                                ),
                        ),
                        const SizedBox(height: 28),
                        ActivityPanel(state: state),
                        const Divider(height: 36),
                        const Text(
                          '设备端实时数据 · 摄像头画面来自 Jetson 设备，教学回答仍为服务端模拟',
                          style: TextStyle(
                            fontSize: 12,
                            color: Color(0xff73817b),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            );
          },
        ),
      ),
    ),
  );
}
