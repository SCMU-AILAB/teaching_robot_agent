// frontend/lib/features/robot_console/widgets/observation_panel.dart
import 'package:flutter/material.dart';

import '../models/robot_console_models.dart';
import '../theme/console_theme.dart';
import 'common.dart';

/// 场景观察面板：按观察状态机展示摘要、物体、关联安全资料与元信息。
class ConsoleObservationPanel extends StatelessWidget {
  const ConsoleObservationPanel({
    required this.session,
    required this.onRefresh,
    super.key,
  });

  /// 当前页面快照。
  final RobotSession session;

  /// 点击「重新观察」时回调；无任务时按钮禁用。
  final VoidCallback onRefresh;

  String _formatCapturedAt(double capturedAt) {
    final DateTime time = DateTime.fromMillisecondsSinceEpoch(
      (capturedAt * 1000).round(),
    );
    String two(int value) => value.toString().padLeft(2, '0');
    return '${two(time.hour)}:${two(time.minute)}:${two(time.second)}';
  }

  Widget _staleBanner() => Container(
    width: double.infinity,
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
    margin: const EdgeInsets.only(bottom: ConsoleSpace.itemGap),
    decoration: BoxDecoration(
      color: ConsoleColors.warningSoft,
      borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
    ),
    child: const Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(Icons.warning_amber_outlined, size: 16, color: ConsoleColors.warning),
        SizedBox(width: 8),
        Expanded(
          child: Text(
            '观察已过期（移动后或超出有效窗口），请重新观察',
            style: TextStyle(
              fontSize: 12,
              height: 1.5,
              color: ConsoleColors.warning,
            ),
          ),
        ),
      ],
    ),
  );

  Widget _objectChip(SceneObject object) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
    decoration: BoxDecoration(
      color: ConsoleColors.accentSoft,
      borderRadius: BorderRadius.circular(ConsoleSpace.radius),
    ),
    child: Text(
      '${object.label}（${object.position}）',
      style: ConsoleText.chipLabel.copyWith(color: ConsoleColors.accent),
    ),
  );

  Widget _safetyCard(SafetyEntry entry) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(12),
    decoration: BoxDecoration(
      color: ConsoleColors.panelSubtle,
      border: Border.all(color: ConsoleColors.border),
      borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(entry.title, style: ConsoleText.cardTitle),
        const SizedBox(height: 4),
        Text(entry.explanation, style: ConsoleText.bodySmall),
        const SizedBox(height: 4),
        Text('来源：${entry.source}', style: ConsoleText.caption),
      ],
    ),
  );

  Widget _succeededBody(SceneObservation observation) {
    final List<Widget> children = <Widget>[
      if (observation.stale) _staleBanner(),
      Text(observation.summary, style: ConsoleText.body),
      const SizedBox(height: ConsoleSpace.itemGap),
      if (observation.objects.isEmpty)
        const Text('未识别出明确物体', style: ConsoleText.caption)
      else
        Wrap(
          spacing: ConsoleSpace.itemGap,
          runSpacing: ConsoleSpace.itemGap,
          children: observation.objects.map(_objectChip).toList(),
        ),
      const SizedBox(height: ConsoleSpace.itemGap),
      if (observation.relatedSafety.isEmpty)
        const Text('资料库没有相关条目', style: ConsoleText.caption)
      else
        ...observation.relatedSafety.map(
          (SafetyEntry entry) => Padding(
            padding: const EdgeInsets.only(bottom: ConsoleSpace.itemGap),
            child: _safetyCard(entry),
          ),
        ),
    ];
    // 示例场景资源仅作展示，与本轮观察结果无直接对应关系。
    if (observation.objects.isNotEmpty) {
      children.addAll(<Widget>[
        ClipRRect(
          borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
          child: Image.asset(
            'assets/scene.png',
            height: 96,
            width: double.infinity,
            fit: BoxFit.cover,
          ),
        ),
        const SizedBox(height: 4),
        const Text(
          '示例场景资源，与本轮观察结果无直接对应关系',
          style: ConsoleText.caption,
        ),
        const SizedBox(height: ConsoleSpace.itemGap),
      ]);
    }
    children.add(
      Wrap(
        spacing: ConsoleSpace.itemGap,
        runSpacing: 4,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          Text(
            '采集时间 ${observation.capturedAt == null ? '未知' : _formatCapturedAt(observation.capturedAt!)}',
            style: ConsoleText.caption,
          ),
          Text('模型 ${observation.modelId}', style: ConsoleText.caption),
          if (observation.simulated)
            const ConsoleStatusChip(label: '模拟观察', status: 'not_configured'),
          if (observation.stale)
            const ConsoleStatusChip(label: '已过期', status: 'pending'),
        ],
      ),
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: children,
    );
  }

  Widget _body() {
    final SceneObservation? observation = session.observation;
    if (observation == null) {
      return const ConsoleEmptyHint(
        icon: Icons.visibility_outlined,
        text: '尚未观察，提问「看看桌上有什么」或点击重新观察',
      );
    }
    switch (observation.status) {
      case 'queued':
      case 'running':
        return const Padding(
          padding: EdgeInsets.symmetric(vertical: 32),
          child: Center(
            child: Column(
              children: [
                SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
                SizedBox(height: 12),
                Text('正在观察…', style: ConsoleText.bodySmall),
              ],
            ),
          ),
        );
      case 'failed':
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 16),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Icon(
                Icons.error_outline,
                size: 18,
                color: ConsoleColors.error,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  observation.error ?? '观察失败，请重试。',
                  style: ConsoleText.bodySmall.copyWith(
                    color: ConsoleColors.error,
                  ),
                ),
              ),
            ],
          ),
        );
      default:
        return _succeededBody(observation);
    }
  }

  @override
  Widget build(BuildContext context) => Container(
    decoration: consolePanelDecoration(),
    padding: const EdgeInsets.all(ConsoleSpace.cardPadding),
    // 有界高度（宽屏侧栏）时观察内容在面板内滚动；无界（窄屏列表、
    // 中屏滚动列）时直接展开，交由外层滚动，避免 Flex 约束冲突。
    child: LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final bool bounded = constraints.maxHeight.isFinite;
        final Widget body = _body();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            ConsoleSectionHeader(
              title: '场景观察',
              trailing: IconButton(
                tooltip: '重新观察',
                onPressed:
                    session.taskActive &&
                        !session.processing &&
                        !session.audioBusy
                    ? onRefresh
                    : null,
                icon: const Icon(Icons.refresh, size: 18),
              ),
            ),
            const SizedBox(height: ConsoleSpace.itemGap),
            if (bounded)
              Expanded(child: SingleChildScrollView(child: body))
            else
              body,
          ],
        );
      },
    ),
  );
}
