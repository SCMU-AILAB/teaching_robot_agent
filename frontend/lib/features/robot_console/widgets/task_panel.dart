// frontend/lib/features/robot_console/widgets/task_panel.dart
import 'package:flutter/material.dart';

import '../models/robot_console_models.dart';
import '../theme/console_theme.dart';
import 'common.dart';

/// 任务面板：创建新任务、展示当前任务状态，并提供验收与停止入口。
///
/// 无任务或任务已到终态时显示目标输入框；任务进行中时显示目标、
/// 状态与操作按钮。验收仅在处理结束且全部动作到达终态后可用。
class ConsoleTaskPanel extends StatelessWidget {
  const ConsoleTaskPanel({
    required this.session,
    required this.busy,
    required this.targetController,
    required this.onCreate,
    required this.onFinish,
    required this.onStop,
    super.key,
  });

  /// 当前页面快照。
  final RobotSession session;

  /// 控制器繁忙标记，用于禁用创建按钮。
  final bool busy;

  /// 任务目标输入框控制器，由页面持有。
  final TextEditingController targetController;

  /// 创建任务回调。
  final VoidCallback onCreate;

  /// 验收任务回调。
  final VoidCallback onFinish;

  /// 停止任务回调。
  final VoidCallback onStop;

  /// 是否存在未到达终态的动作。
  bool get _hasPendingAction =>
      session.actions.any((RobotAction a) => !a.terminal);

  /// 验收按钮可用条件：无处理、无音频占用、动作全部终态、非停止中。
  bool get _finishEnabled =>
      session.taskActive &&
      !session.processing &&
      !session.audioBusy &&
      !_hasPendingAction &&
      session.taskStatus != 'cancelling';

  /// 停止按钮仅在 pending/running 时可用；cancelling 显示「停止中…」。
  bool get _stopEnabled =>
      session.taskStatus == 'pending' || session.taskStatus == 'running';

  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(ConsoleSpace.cardPadding),
    decoration: consolePanelDecoration(),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ConsoleSectionHeader(
          title: '任务',
          trailing: session.hasTask
              ? ConsoleStatusChip(
                  label: consoleStatusText(session.taskStatus),
                  status: session.taskStatus,
                )
              : null,
        ),
        const SizedBox(height: 12),
        if (session.robot.blockedReason != null) ...[
          _BlockedReasonBar(reason: session.robot.blockedReason!),
          const SizedBox(height: 12),
        ],
        // 任务进行中：展示目标与操作；终态：展示结果说明。
        if (session.taskActive)
          _ActiveTaskBody(
            session: session,
            finishEnabled: _finishEnabled,
            stopEnabled: _stopEnabled,
            onFinish: onFinish,
            onStop: onStop,
          )
        else ...[
          if (session.hasTask) ...[
            Text(session.taskTarget, style: ConsoleText.cardTitle),
            const SizedBox(height: 8),
            Text(session.note, style: ConsoleText.bodySmall),
            const SizedBox(height: 12),
          ],
          _CreateTaskRow(
            controller: targetController,
            busy: busy,
            onCreate: onCreate,
          ),
        ],
      ],
    ),
  );
}

/// 阻塞原因提示条：机器人被占用或资源不可用时说明原因。
class _BlockedReasonBar extends StatelessWidget {
  const _BlockedReasonBar({required this.reason});

  final String reason;

  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
    decoration: BoxDecoration(
      color: ConsoleColors.errorSoft,
      borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
    ),
    child: Row(
      children: [
        const Icon(
          Icons.error_outline,
          size: 16,
          color: ConsoleColors.error,
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            reason,
            style: ConsoleText.bodySmall.copyWith(color: ConsoleColors.error),
          ),
        ),
      ],
    ),
  );
}

/// 进行中的任务：目标、动作计数、验收与停止按钮。
class _ActiveTaskBody extends StatelessWidget {
  const _ActiveTaskBody({
    required this.session,
    required this.finishEnabled,
    required this.stopEnabled,
    required this.onFinish,
    required this.onStop,
  });

  final RobotSession session;
  final bool finishEnabled;
  final bool stopEnabled;
  final VoidCallback onFinish;
  final VoidCallback onStop;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(session.taskTarget, style: ConsoleText.cardTitle),
      const SizedBox(height: 6),
      Text(
        '已记录 ${session.actions.length} 个动作',
        style: ConsoleText.caption,
      ),
      const SizedBox(height: 12),
      Wrap(
        spacing: 12,
        runSpacing: 8,
        children: [
          Tooltip(
            message: '处理中或动作未结束时不能验收',
            child: FilledButton.icon(
              onPressed: finishEnabled ? onFinish : null,
              icon: const Icon(Icons.check_circle_outline, size: 18),
              label: const Text('验收任务'),
            ),
          ),
          OutlinedButton.icon(
            onPressed: stopEnabled ? onStop : null,
            icon: const Icon(Icons.stop_outlined, size: 18),
            label: Text(
              session.taskStatus == 'cancelling' ? '停止中…' : '停止任务',
            ),
          ),
        ],
      ),
    ],
  );
}

/// 无任务时的目标输入与创建按钮。
class _CreateTaskRow extends StatelessWidget {
  const _CreateTaskRow({
    required this.controller,
    required this.busy,
    required this.onCreate,
  });

  final TextEditingController controller;
  final bool busy;
  final VoidCallback onCreate;

  @override
  Widget build(BuildContext context) => Row(
    children: [
      Expanded(
        child: TextField(
          key: const Key('target_input'),
          controller: controller,
          maxLength: 2000,
          // 隐藏字数计数，保持工作台简洁；上限由 maxLength 强制。
          buildCounter:
              (
                _, {
                required int currentLength,
                required bool isFocused,
                required int? maxLength,
              }) => null,
          decoration: const InputDecoration(
            hintText: '例如：看看桌上有什么，讲讲安全知识',
          ),
          onSubmitted: (_) => onCreate(),
        ),
      ),
      const SizedBox(width: 12),
      FilledButton.icon(
        key: const Key('create_button'),
        onPressed: busy ? null : onCreate,
        icon: const Icon(Icons.add_task, size: 18),
        label: const Text('创建任务'),
      ),
    ],
  );
}
