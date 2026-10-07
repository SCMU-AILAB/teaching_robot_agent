// frontend/lib/features/classroom/classroom_page.dart
import 'dart:async';

import 'package:flutter/material.dart';

import 'classroom_controller.dart';
import 'mock_classroom_service.dart';
import 'session_model.dart';
import 'widgets/activity_panel.dart';
import 'widgets/conversation_panel.dart';
import 'widgets/observation_panel.dart';
import 'widgets/status_label.dart';

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

  @override
  void initState() {
    super.initState();
    _controller =
        widget.controller ?? ClassroomController(MockClassroomService());
    unawaited(_controller.initialize());
  }

  @override
  void dispose() {
    _target.dispose();
    _message.dispose();
    if (widget.controller == null) _controller.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final bool accepted = await _controller.submitText(_message.text);
    if (accepted && mounted) _message.clear();
  }

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
            Icons.smart_toy_outlined,
            size: 28,
            color: Color(0xff147d64),
          ),
          const SizedBox(width: 12),
          const Text(
            '教学机器人',
            style: TextStyle(fontWeight: FontWeight.w700, fontSize: 21),
          ),
          const SizedBox(width: 12),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            color: const Color(0xffe6eee9),
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
            Icons.circle,
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
            icon: const Icon(Icons.tune, size: 20),
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
              _controller.connected ? Icons.wifi_off_outlined : Icons.refresh,
              size: 20,
            ),
          ),
        ],
      ),
    ],
  );

  Widget _notice(String text, {bool error = false}) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(14),
    margin: const EdgeInsets.only(bottom: 16),
    color: error ? const Color(0xffffefed) : const Color(0xffeaf1ef),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(
          error ? Icons.error_outline : Icons.info_outline,
          size: 18,
          color: error ? const Color(0xffb6403a) : const Color(0xff147d64),
        ),
        const SizedBox(width: 10),
        Expanded(child: Text(text, style: const TextStyle(fontSize: 13))),
        if (!_controller.connected)
          TextButton(
            onPressed: () => unawaited(_controller.reconnect()),
            child: const Text('重新连接'),
          ),
      ],
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
    final Widget buttons = Wrap(
      spacing: 10,
      runSpacing: 10,
      children: [
        FilledButton.icon(
          key: const Key('start-task'),
          onPressed: state.active || _controller.busy || !_controller.connected
              ? null
              : () => unawaited(_controller.createTask(_target.text)),
          icon: const Icon(Icons.play_arrow, size: 20),
          label: const Text('开始课堂'),
        ),
        OutlinedButton.icon(
          key: const Key('stop-task'),
          onPressed: state.active && state.taskStatus != 'cancelling'
              ? () => unawaited(_controller.stop())
              : null,
          style: OutlinedButton.styleFrom(
            foregroundColor: const Color(0xffb6403a),
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 18),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(6),
            ),
          ),
          icon: const Icon(Icons.stop, size: 20),
          label: Text(state.taskStatus == 'cancelling' ? '正在停止' : '停止课堂'),
        ),
      ],
    );
    return compact
        ? Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [target, const SizedBox(height: 12), buttons],
          )
        : Row(
            children: [
              Expanded(child: target),
              const SizedBox(width: 16),
              buttons,
            ],
          );
  }

  Widget _composer() {
    final SessionModel state = _controller.state;
    final bool recording = state.recording == 'recording';
    final bool audioBusy =
        ['playing', 'synthesizing'].contains(state.playback) ||
        state.recording == 'transcribing';
    final bool available =
        state.taskStatus == 'running' &&
        _controller.connected &&
        !_controller.busy;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: 20),
        Row(
          children: [
            Expanded(
              child: TextField(
                key: const Key('message-input'),
                controller: _message,
                enabled: available && !audioBusy && !recording,
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
            const SizedBox(width: 8),
            IconButton.filled(
              key: const Key('send-message'),
              tooltip: '发送文字',
              onPressed: available && !audioBusy && !recording
                  ? () => unawaited(_send())
                  : null,
              icon: const Icon(Icons.arrow_upward),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 16,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            OutlinedButton.icon(
              key: const Key('record-button'),
              onPressed: available && !audioBusy
                  ? () => unawaited(_controller.record())
                  : null,
              icon: Icon(recording ? Icons.stop : Icons.mic_none),
              label: Text(recording ? '结束录音' : '开始录音'),
            ),
            StatusLabel(state.recording),
            if (recording)
              Text(
                '${state.recordingSeconds.toString().padLeft(2, '0')} / 15 秒',
                style: const TextStyle(fontSize: 12, color: Color(0xffb6403a)),
              ),
          ],
        ),
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
                ConversationPanel(state: state),
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
                        if (_controller.error != null)
                          _notice(_controller.error!, error: true),
                        if (state.note.isNotEmpty)
                          _notice(
                            state.note,
                            error:
                                state.recording == 'failed' ||
                                state.playback == 'failed',
                          ),
                        Container(
                          color: Colors.white,
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
