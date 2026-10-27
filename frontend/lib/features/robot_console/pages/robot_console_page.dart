// frontend/lib/features/robot_console/pages/robot_console_page.dart
import 'dart:async';

import 'package:flutter/material.dart';

import '../controllers/robot_console_controller.dart';
import '../models/robot_console_models.dart';
import '../services/mock_robot_console_service.dart';
import '../services/robot_console_service.dart';
import '../theme/console_theme.dart';
import '../widgets/action_panel.dart';
import '../widgets/conversation_panel.dart';
import '../widgets/observation_panel.dart';
import '../widgets/status_bar.dart';
import '../widgets/task_panel.dart';

/// 家庭学习陪伴与安全教育机器人主控制台页面。
///
/// 默认使用本地模拟数据源；通过 [service] 或 [controller] 可注入
/// 测试替身或后续按审核后的 API 合同实现的 HTTP 数据源。
/// 布局按宽度分三档：>=1180 三栏、760–1180 双栏、<760 纵向列表。
class RobotConsolePage extends StatefulWidget {
  const RobotConsolePage({this.service, this.controller, super.key});

  /// 可选数据源；未传入 controller 时用于构造默认控制器。
  final RobotConsoleService? service;

  /// 可选控制器，优先于 [service]；传入时页面不负责释放。
  final RobotConsoleController? controller;

  @override
  State<RobotConsolePage> createState() => _RobotConsolePageState();
}

class _RobotConsolePageState extends State<RobotConsolePage> {
  late final RobotConsoleController _controller;
  final TextEditingController _target = TextEditingController(
    text: '看看桌上有什么，讲讲安全知识',
  );
  final TextEditingController _message = TextEditingController();

  @override
  void initState() {
    super.initState();
    // 优先使用外部注入的控制器；否则按传入服务或默认 Mock 自建。
    _controller =
        widget.controller ??
        RobotConsoleController(widget.service ?? MockRobotConsoleService());
    unawaited(_controller.initialize());
  }

  @override
  void dispose() {
    _target.dispose();
    _message.dispose();
    // 只释放本页面自建的控制器，外部注入的由调用方管理。
    if (widget.controller == null) _controller.dispose();
    super.dispose();
  }

  /// 发送成功后清空输入框，便于连续追问。
  Future<void> _send() async {
    final bool accepted = await _controller.submitText(_message.text);
    if (accepted && mounted) _message.clear();
  }

  void _toggleConnection() {
    if (_controller.connected) {
      _controller.disconnect();
    } else {
      unawaited(_controller.reconnect());
    }
  }

  Widget _errorBanner(String text) => Container(
    width: double.infinity,
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
    decoration: BoxDecoration(
      color: ConsoleColors.errorSoft,
      border: Border.all(color: ConsoleColors.border),
      borderRadius: BorderRadius.circular(ConsoleSpace.radius),
    ),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Icon(Icons.error_outline, size: 16, color: ConsoleColors.error),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            text,
            style: ConsoleText.bodySmall.copyWith(color: ConsoleColors.error),
          ),
        ),
      ],
    ),
  );

  Widget _buildBody(BuildContext context) {
    final RobotSession session = _controller.state;
    final Widget statusBar = ConsoleStatusBar(
      key: const Key('status_bar'),
      session: session,
      connected: _controller.connected,
      busy: _controller.busy,
      scenario: _controller.scenario,
      onToggleConnection: _toggleConnection,
      onSelectScenario: _controller.selectScenario,
    );
    final Widget taskPanel = ConsoleTaskPanel(
      key: const Key('task_panel'),
      session: session,
      busy: _controller.busy,
      targetController: _target,
      onCreate: () => unawaited(_controller.createTask(_target.text)),
      onFinish: () => unawaited(_controller.finishTask()),
      onStop: () => unawaited(_controller.stop()),
    );
    final Widget conversationPanel = ConsoleConversationPanel(
      key: const Key('conversation_panel'),
      session: session,
      busy: _controller.busy,
      messageController: _message,
      onSend: () => unawaited(_send()),
      onRecord: () => unawaited(_controller.record()),
    );
    final Widget observationPanel = ConsoleObservationPanel(
      key: const Key('observation_panel'),
      session: session,
      onRefresh: () => unawaited(_controller.refresh()),
    );
    final Widget actionPanel = ConsoleActionPanel(
      key: const Key('action_panel'),
      session: session,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        statusBar,
        if (_controller.error != null) ...[
          const SizedBox(height: ConsoleSpace.panelGap),
          _errorBanner(_controller.error!),
        ],
        const SizedBox(height: ConsoleSpace.panelGap),
        taskPanel,
        const SizedBox(height: ConsoleSpace.panelGap),
        Expanded(
          child: LayoutBuilder(
            builder: (BuildContext context, BoxConstraints constraints) {
              final double width = constraints.maxWidth;
              if (width >= 1180) {
                // 宽屏三栏：观察 | 对话 | 动作。
                return Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    SizedBox(width: 330, child: observationPanel),
                    const SizedBox(width: ConsoleSpace.panelGap),
                    Expanded(child: conversationPanel),
                    const SizedBox(width: ConsoleSpace.panelGap),
                    SizedBox(width: 330, child: actionPanel),
                  ],
                );
              }
              if (width >= 760) {
                // 中屏双栏：对话为主，观察与动作在右侧纵向滚动。
                return Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Expanded(child: conversationPanel),
                    const SizedBox(width: ConsoleSpace.panelGap),
                    SizedBox(
                      width: 340,
                      child: SingleChildScrollView(
                        child: Column(
                          children: [
                            observationPanel,
                            const SizedBox(height: ConsoleSpace.panelGap),
                            actionPanel,
                          ],
                        ),
                      ),
                    ),
                  ],
                );
              }
              // 窄屏纵向：对话固定高度，观察与动作依次排列；面板数量固定，一次构建全部。
              return SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    SizedBox(height: 440, child: conversationPanel),
                    const SizedBox(height: ConsoleSpace.panelGap),
                    observationPanel,
                    const SizedBox(height: ConsoleSpace.panelGap),
                    actionPanel,
                  ],
                ),
              );
            },
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: _controller,
    builder: (BuildContext context, Widget? child) => Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(ConsoleSpace.pagePadding),
          child: _buildBody(context),
        ),
      ),
    ),
  );
}
