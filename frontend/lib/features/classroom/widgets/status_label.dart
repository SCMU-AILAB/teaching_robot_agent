// frontend/lib/features/classroom/widgets/status_label.dart
import 'package:flutter/material.dart';

/// 将技术状态转换为操作者可读的文字，不改变领域状态含义。
class StatusLabel extends StatelessWidget {
  const StatusLabel(this.status, {super.key});
  final String status;

  static const Map<String, String> labels = {
    'idle': '未开始',
    'running': '进行中',
    'queued': '排队中',
    'verifying': '验证中',
    'succeeded': '已完成',
    'completed': '已完成',
    'cancelling': '正在停止',
    'cancelled': '已取消',
    'failed': '失败',
    'timed_out': '已超时',
    'recording': '录音中',
    'transcribing': '识别中',
    'synthesizing': '合成中',
    'playing': '播放中',
    'stopped': '已停止',
    'not_started': '准备开始',
    'explaining': '讲解中',
    'awaiting_answer': '等待回答',
    'feedback': '反馈中',
  };

  @override
  Widget build(BuildContext context) {
    final bool failed = status == 'failed' || status == 'timed_out';
    final bool active = [
      'playing',
      'running',
      'recording',
      'explaining',
      'awaiting_answer',
    ].contains(status);
    final Color color = failed
        ? const Color(0xffb6403a)
        : active
        ? const Color(0xff147d64)
        : const Color(0xff63717a);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.circle, size: 7, color: color),
        const SizedBox(width: 7),
        Text(
          labels[status] ?? status,
          style: TextStyle(
            fontSize: 12,
            color: color,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }
}
