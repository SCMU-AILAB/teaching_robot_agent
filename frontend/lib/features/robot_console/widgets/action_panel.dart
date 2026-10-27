// frontend/lib/features/robot_console/widgets/action_panel.dart
import 'package:flutter/material.dart';

import '../models/robot_console_models.dart';
import '../theme/console_theme.dart';
import 'common.dart';

/// 动作时间线面板：按顺序展示任务内的动作记录与终态结果。
///
/// 动作被接受不代表执行成功，面板只读展示，不提供独立取消按钮；
/// 取消统一由任务面板的停止入口负责。
///
/// 面板可能在有界高度（宽屏侧栏）或无界滚动上下文（窄屏列表、
/// 中屏滚动列）中使用：有界时列表占满剩余空间并自行滚动，
/// 无界时改用 shrinkWrap 列表交由外层滚动，避免 Flex 溢出崩溃。
class ConsoleActionPanel extends StatelessWidget {
  const ConsoleActionPanel({required this.session, super.key});

  /// 页面级快照，读取其中的动作列表。
  final RobotSession session;

  @override
  Widget build(BuildContext context) {
    final List<RobotAction> actions = session.actions;
    return Container(
      decoration: consolePanelDecoration(),
      padding: const EdgeInsets.all(ConsoleSpace.cardPadding),
      child: LayoutBuilder(
        builder: (BuildContext context, BoxConstraints constraints) {
          // 无界高度说明外层负责滚动，此时列表不能使用 Expanded。
          final bool bounded = constraints.maxHeight.isFinite;
          final Widget list;
          if (actions.isEmpty) {
            list = const ConsoleEmptyHint(
              icon: Icons.play_circle_outline,
              text: '暂无动作记录',
            );
          } else {
            list = ListView.separated(
              itemCount: actions.length,
              shrinkWrap: !bounded,
              physics: bounded ? null : const NeverScrollableScrollPhysics(),
              separatorBuilder: (_, _) =>
                  const SizedBox(height: ConsoleSpace.itemGap),
              itemBuilder: (BuildContext context, int index) =>
                  _ActionTimelineItem(action: actions[index]),
            );
          }
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const ConsoleSectionHeader(title: '动作时间线'),
              const SizedBox(height: ConsoleSpace.itemGap),
              const Text('动作被接受不代表执行成功', style: ConsoleText.caption),
              const SizedBox(height: ConsoleSpace.itemGap),
              if (bounded) Expanded(child: list) else list,
            ],
          );
        },
      ),
    );
  }
}

/// 单个动作时间线条目：序号圆点 + 技能名 + 参数 + 状态 + 结果或失败原因。
class _ActionTimelineItem extends StatelessWidget {
  const _ActionTimelineItem({required this.action});

  final RobotAction action;

  @override
  Widget build(BuildContext context) {
    final Color color = consoleStatusColor(action.status);
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: ConsoleColors.panelSubtle,
        borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
        border: Border.all(color: ConsoleColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              // 序号圆点：颜色随状态变化，同时用文字序号避免只靠颜色传达。
              Container(
                width: 20,
                height: 20,
                alignment: Alignment.center,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
                child: Text(
                  '${action.actionId}',
                  style: ConsoleText.chipLabel.copyWith(
                    color: ConsoleColors.panel,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(action.skillName, style: ConsoleText.cardTitle),
              ),
              ConsoleStatusChip(
                label: consoleStatusText(action.status),
                status: action.status,
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(action.argsSummary, style: ConsoleText.caption),
          if (action.resultSummary != null) ...[
            const SizedBox(height: 4),
            Text(action.resultSummary!, style: ConsoleText.bodySmall),
          ],
          if (action.failure != null) ...[
            const SizedBox(height: 4),
            Text(
              action.failure!,
              style: ConsoleText.bodySmall.copyWith(color: ConsoleColors.error),
            ),
          ],
        ],
      ),
    );
  }
}
