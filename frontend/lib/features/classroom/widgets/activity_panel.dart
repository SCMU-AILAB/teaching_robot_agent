// frontend/lib/features/classroom/widgets/activity_panel.dart
import 'package:flutter/material.dart';

import '../session_model.dart';
import 'status_label.dart';

/// 只读呈现教学进度和动作反馈，不提供任意动作执行入口。
class ActivityPanel extends StatelessWidget {
  const ActivityPanel({required this.state, super.key});
  final SessionModel state;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text('课堂进度', style: Theme.of(context).textTheme.titleMedium),
      const SizedBox(height: 16),
      Wrap(
        spacing: 20,
        runSpacing: 12,
        children: [
          for (final (int index, String title, bool active) step in [
            (1, '场景观察', state.observed),
            (2, '讲解与播报', state.messages.isNotEmpty),
            (
              3,
              '学生回答',
              state.stage == 'awaiting_answer' ||
                  state.messages.any((ClassMessage m) => m.role == 'student'),
            ),
          ])
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                CircleAvatar(
                  radius: 12,
                  backgroundColor: step.$3
                      ? const Color(0xff147d64)
                      : const Color(0xffe4e9eb),
                  child: Text(
                    '${step.$1}',
                    style: TextStyle(
                      fontSize: 11,
                      color: step.$3 ? Colors.white : const Color(0xff6c7980),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Text(step.$2, style: const TextStyle(fontSize: 13)),
              ],
            ),
        ],
      ),
      const SizedBox(height: 24),
      const Divider(),
      const SizedBox(height: 10),
      Row(
        children: [
          const Text('动作记录', style: TextStyle(fontWeight: FontWeight.w600)),
          const Spacer(),
          Text(
            '${state.actions.length} 项',
            style: const TextStyle(fontSize: 12, color: Color(0xff73817b)),
          ),
        ],
      ),
      if (state.actions.isEmpty)
        const Padding(
          padding: EdgeInsets.symmetric(vertical: 20),
          child: Text('暂无动作', style: TextStyle(color: Color(0xff73817b))),
        ),
      for (final ClassAction action in state.actions.reversed.take(6))
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 11),
          child: Row(
            children: [
              const Icon(
                Icons.chevron_right,
                size: 17,
                color: Color(0xff83908d),
              ),
              const SizedBox(width: 8),
              Expanded(child: Text(action.label)),
              StatusLabel(action.status),
            ],
          ),
        ),
    ],
  );
}
