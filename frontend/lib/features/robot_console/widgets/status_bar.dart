// frontend/lib/features/robot_console/widgets/status_bar.dart
import 'package:flutter/material.dart';

import '../models/robot_console_models.dart';
import '../theme/console_theme.dart';
import 'common.dart';

/// 控制台顶部状态条：项目标识、机器人状态、服务状态与演示控制。
class ConsoleStatusBar extends StatelessWidget {
  const ConsoleStatusBar({
    required this.session,
    required this.connected,
    required this.busy,
    required this.scenario,
    required this.onToggleConnection,
    required this.onSelectScenario,
    super.key,
  });

  /// 当前会话快照。
  final RobotSession session;

  /// 与数据源是否保持连接。
  final bool connected;

  /// 是否有请求正在处理。
  final bool busy;

  /// 当前演示场景。
  final ConsoleScenario scenario;

  /// 切换模拟断线/重连。
  final VoidCallback onToggleConnection;

  /// 切换演示场景。
  final ValueChanged<ConsoleScenario> onSelectScenario;

  /// 演示场景的展示文案。
  static const Map<ConsoleScenario, String> _scenarioLabels =
      <ConsoleScenario, String>{
        ConsoleScenario.normal: '正常交互',
        ConsoleScenario.silence: '无有效语音',
        ConsoleScenario.asrFailure: '识别失败',
        ConsoleScenario.speechFailure: '合成失败',
        ConsoleScenario.requestFailure: '请求失败',
        ConsoleScenario.emptyObservation: '空观察',
      };

  @override
  Widget build(BuildContext context) {
    final RobotStatus robot = session.robot;
    return Container(
      decoration: consolePanelDecoration(),
      padding: const EdgeInsets.symmetric(
        horizontal: ConsoleSpace.cardPadding,
        vertical: 12,
      ),
      // 窄空间下 Wrap 自动换行，避免三段内容溢出。
      child: Wrap(
        alignment: WrapAlignment.spaceBetween,
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: 24,
        runSpacing: 12,
        children: [
          _brand(),
          _robotStatus(robot),
          _controls(),
        ],
      ),
    );
  }

  /// 左侧：项目图标、名称与常显模拟标记；Wrap 在窄屏自动换行防溢出。
  Widget _brand() => Wrap(
    spacing: 10,
    runSpacing: 8,
    crossAxisAlignment: WrapCrossAlignment.center,
    children: [
      const Icon(
        Icons.smart_toy_outlined,
        size: 26,
        color: ConsoleColors.accent,
      ),
      const Text(
        '家庭学习陪伴与安全教育机器人',
        style: TextStyle(
          fontSize: 17,
          fontWeight: FontWeight.w700,
          color: ConsoleColors.textPrimary,
        ),
      ),
      const ConsoleModeBadge(),
    ],
  );

  /// 中间：连接圆点（含文字）、运动状态、位姿与模型弱提示。
  Widget _robotStatus(RobotStatus robot) {
    final Color dotColor = connected
        ? ConsoleColors.success
        : ConsoleColors.error;
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        // 连接状态同时用圆点颜色与文字表达，不依赖颜色单一传达。
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: dotColor, shape: BoxShape.circle),
        ),
        Text(
          connected ? '已连接' : '已断线',
          style: ConsoleText.bodySmall.copyWith(color: dotColor),
        ),
        Icon(
          robot.isMoving ? Icons.directions_run : Icons.stop_circle_outlined,
          size: 16,
          color: robot.isMoving
              ? ConsoleColors.info
              : ConsoleColors.textMuted,
        ),
        Text(robot.isMoving ? '移动中' : '静止', style: ConsoleText.bodySmall),
        if (robot.poseText != null)
          Text(robot.poseText!, style: ConsoleText.caption),
        Text('模型 ${robot.modelId}', style: ConsoleText.caption),
      ],
    );
  }

  /// 右侧：三项服务状态、演示场景菜单与连接开关；Wrap 防窄屏溢出。
  Widget _controls() {
    final RobotStatus robot = session.robot;
    return Wrap(
      spacing: ConsoleSpace.itemGap,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        ConsoleStatusChip(
          label: '感知 ${consoleStatusText(robot.perception)}',
          status: robot.perception,
        ),
        ConsoleStatusChip(
          label: '识别 ${consoleStatusText(robot.asr)}',
          status: robot.asr,
        ),
        ConsoleStatusChip(
          label: '合成 ${consoleStatusText(robot.tts)}',
          status: robot.tts,
        ),
        PopupMenuButton<ConsoleScenario>(
          tooltip: '演示场景',
          initialValue: scenario,
          onSelected: onSelectScenario,
          itemBuilder: (BuildContext context) => _scenarioLabels.entries
              .map(
                (MapEntry<ConsoleScenario, String> entry) =>
                    PopupMenuItem<ConsoleScenario>(
                      value: entry.key,
                      child: Text(entry.value),
                    ),
              )
              .toList(),
          child: Container(
            height: ConsoleSpace.buttonHeight,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            decoration: BoxDecoration(
              color: ConsoleColors.panelSubtle,
              border: Border.all(color: ConsoleColors.border),
              borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(
                  Icons.science_outlined,
                  size: 16,
                  color: ConsoleColors.textSecondary,
                ),
                const SizedBox(width: 6),
                Text(
                  _scenarioLabels[scenario] ?? '演示场景',
                  style: ConsoleText.bodySmall,
                ),
                const Icon(
                  Icons.arrow_drop_down,
                  size: 18,
                  color: ConsoleColors.textSecondary,
                ),
              ],
            ),
          ),
        ),
        // 请求处理中禁止切换连接，避免命令与断线状态交错。
        OutlinedButton.icon(
          onPressed: busy ? null : onToggleConnection,
          icon: Icon(
            connected ? Icons.link_off : Icons.link,
            size: 16,
          ),
          label: Text(connected ? '模拟断线' : '重新连接'),
        ),
      ],
    );
  }
}
