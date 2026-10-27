// frontend/lib/features/robot_console/widgets/common.dart
import 'package:flutter/material.dart';
import 'package:teaching_robot_frontend/features/robot_console/theme/console_theme.dart';

/// 状态码对应的状态色；不只用颜色传达状态，必须配合文字或图标。
Color consoleStatusColor(String status) {
  switch (status) {
    case 'running':
    case 'queued':
    case 'playing':
      return ConsoleColors.info;
    case 'pending':
    case 'verifying':
    case 'cancelling':
    case 'transcribing':
    case 'synthesizing':
      return ConsoleColors.warning;
    case 'succeeded':
    case 'completed':
    case 'ready':
      return ConsoleColors.success;
    case 'failed':
    case 'timed_out':
    case 'unavailable':
      return ConsoleColors.error;
    default:
      // idle/cancelled/stopped/not_configured 及其余未知状态。
      return ConsoleColors.textMuted;
  }
}

/// 状态码对应的中文文案。
String consoleStatusText(String status) {
  switch (status) {
    case 'queued':
      return '排队中';
    case 'running':
      return '执行中';
    case 'verifying':
      return '验证中';
    case 'cancelling':
      return '停止中';
    case 'succeeded':
      return '已完成';
    case 'failed':
      return '失败';
    case 'cancelled':
      return '已取消';
    case 'timed_out':
      return '超时';
    case 'idle':
      return '空闲';
    case 'pending':
      return '待处理';
    case 'completed':
      return '已完成';
    case 'recording':
      return '录音中';
    case 'transcribing':
      return '识别中';
    case 'synthesizing':
      return '合成中';
    case 'playing':
      return '播放中';
    case 'stopped':
      return '已停止';
    case 'ready':
      return '就绪';
    case 'unavailable':
      return '不可用';
    case 'not_configured':
      return '未配置';
    default:
      return status;
  }
}

/// 状态色对应的浅底色。
Color _consoleStatusSoftColor(String status) {
  switch (status) {
    case 'running':
    case 'queued':
    case 'playing':
      return ConsoleColors.infoSoft;
    case 'pending':
    case 'verifying':
    case 'cancelling':
    case 'transcribing':
    case 'synthesizing':
      return ConsoleColors.warningSoft;
    case 'succeeded':
    case 'completed':
    case 'ready':
      return ConsoleColors.successSoft;
    case 'failed':
    case 'timed_out':
    case 'unavailable':
      return ConsoleColors.errorSoft;
    default:
      return ConsoleColors.panelSubtle;
  }
}

/// 胶囊状态标签：前置状态圆点 + 状态色文字 + 浅底背景。
class ConsoleStatusChip extends StatelessWidget {
  const ConsoleStatusChip({
    required this.label,
    required this.status,
    super.key,
  });

  /// 展示文案（如「执行中」）。
  final String label;

  /// 状态码，决定圆点与文字颜色。
  final String status;

  @override
  Widget build(BuildContext context) {
    final color = consoleStatusColor(status);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: _consoleStatusSoftColor(status),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 6),
          Text(label, style: ConsoleText.chipLabel.copyWith(color: color)),
        ],
      ),
    );
  }
}

/// 分区标题行：左侧标题，右侧可选操作区。
class ConsoleSectionHeader extends StatelessWidget {
  const ConsoleSectionHeader({required this.title, this.trailing, super.key});

  /// 分区标题。
  final String title;

  /// 尾部操作（按钮、状态标签等）。
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(child: Text(title, style: ConsoleText.sectionTitle)),
        ?trailing,
      ],
    );
  }
}

/// 空状态提示：居中图标与弱提示文案。
class ConsoleEmptyHint extends StatelessWidget {
  const ConsoleEmptyHint({required this.icon, required this.text, super.key});

  /// 提示图标。
  final IconData icon;

  /// 提示文案。
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 32),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 32, color: ConsoleColors.textMuted),
            const SizedBox(height: ConsoleSpace.itemGap),
            Text(text, style: ConsoleText.caption, textAlign: TextAlign.center),
          ],
        ),
      ),
    );
  }
}

/// 模拟模式标记：常显「本地模拟 · 未连接真实机器人」。
class ConsoleModeBadge extends StatelessWidget {
  const ConsoleModeBadge({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: ConsoleColors.warningSoft,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.tune, size: 14, color: ConsoleColors.warning),
          const SizedBox(width: 6),
          Text(
            '本地模拟 · 未连接真实机器人',
            style: ConsoleText.chipLabel.copyWith(color: ConsoleColors.warning),
          ),
        ],
      ),
    );
  }
}
