// frontend/lib/features/classroom/classroom_page.dart
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

import '../../core/constants/app_radius.dart';
import 'classroom_controller.dart';
import 'mock_classroom_service.dart';
import 'session_model.dart';
import 'widgets/activity_panel.dart';
import 'widgets/conversation_panel.dart';
import 'widgets/field_error_box.dart';
import 'widgets/observation_panel.dart';
import 'widgets/record_toggle_button.dart';
import 'widgets/rolling_text.dart';
import 'widgets/send_button.dart';
import 'widgets/status_label.dart';
import 'widgets/task_toggle_button.dart';

/// 自适应课堂操作页面，默认使用独立本地模拟数据源。
class ClassroomPage extends StatefulWidget {
  const ClassroomPage({this.controller, super.key});
  final ClassroomController? controller;

  @override
  State<ClassroomPage> createState() => _ClassroomPageState();
}

class _ClassroomPageState extends State<ClassroomPage> {
  late final ClassroomController _controller;
  final TextEditingController _target = TextEditingController(text: '认识桌面物体');
  final TextEditingController _message = TextEditingController();
  final GlobalKey _messageInputKey = GlobalKey();

  /// 发送形变进行中：隐藏真实输入框，让「整框变小变蓝」可见。
  final ValueNotifier<bool> _sendMorphing = ValueNotifier<bool>(false);

  @override
  void initState() {
    super.initState();
    _controller =
        widget.controller ?? ClassroomController(MockClassroomService());
    unawaited(_controller.initialize());
  }

  @override
  void dispose() {
    _sendMorphing.dispose();
    _target.dispose();
    _message.dispose();
    if (widget.controller == null) _controller.dispose();
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
            child: const Text(
              '模拟课堂',
              style: TextStyle(fontSize: 11, color: Color(0xff147d64)),
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
            color: _controller.connected
                ? const Color(0xff147d64)
                : const Color(0xffb6403a),
          ),
          const SizedBox(width: 7),
          Text(
            _controller.connected ? '模拟连接正常' : '连接已断开',
            style: const TextStyle(fontSize: 12),
          ),
          const SizedBox(width: 8),
          PopupMenuButton<DemoScenario>(
            tooltip: '演示场景',
            icon: Icon(Symbols.tune_rounded, size: 20),
            initialValue: _controller.scenario,
            onSelected: _controller.selectScenario,
            itemBuilder: (_) => const [
              PopupMenuItem(value: DemoScenario.normal, child: Text('正常交互')),
              PopupMenuItem(value: DemoScenario.silence, child: Text('无有效语音')),
              PopupMenuItem(
                value: DemoScenario.asrFailure,
                child: Text('识别失败'),
              ),
              PopupMenuItem(
                value: DemoScenario.synthesisFailure,
                child: Text('合成失败'),
              ),
              PopupMenuItem(
                value: DemoScenario.requestFailure,
                child: Text('请求失败'),
              ),
            ],
          ),
          IconButton(
            tooltip: _controller.connected ? '模拟断线' : '重新连接',
            onPressed: () {
              if (_controller.connected) {
                _controller.disconnect();
              } else {
                unawaited(_controller.reconnect());
              }
            },
            icon: Icon(
              _controller.connected
                  ? Symbols.wifi_off_rounded
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
      decoration: const InputDecoration(labelText: '课堂目标', counterText: ''),
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
    final bool audioBusy =
        !state.observed ||
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
                          '本地模拟会话 · 摄像头、录音与播报均为演示数据',
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
