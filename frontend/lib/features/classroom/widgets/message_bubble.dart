// frontend/lib/features/classroom/widgets/message_bubble.dart
import 'package:flutter/material.dart';

/// iMessage 风格消息气泡：苹果连续圆角（squircle），尾角一侧收紧。
///
/// 机器人老师用主题绿，学生用 iMessage 蓝；文字均为白色。
class MessageBubble extends StatelessWidget {
  const MessageBubble({
    required this.text,
    required this.student,
    this.maxWidth = 300,
    super.key,
  });

  /// 气泡文本。
  final String text;

  /// 是否学生发送的气泡（右侧蓝色）。
  final bool student;

  /// 换行宽度上限：学生气泡传入输入框内容宽 + 内边距，
  /// 保证气泡断行与输入框一致（飞行中一行字永远一行）。
  final double maxWidth;

  /// 学生气泡：iMessage 蓝。
  static const Color studentColor = Color(0xff248bf5);

  /// 老师气泡：主题绿。
  static const Color teacherColor = Color(0xff147d64);

  @override
  Widget build(BuildContext context) {
    // 气泡尾巴一侧的下角收紧，其余为大圆角连续曲率。
    final BorderRadius radius = BorderRadius.only(
      topLeft: const Radius.circular(20),
      topRight: const Radius.circular(20),
      bottomLeft: Radius.circular(student ? 20 : 6),
      bottomRight: Radius.circular(student ? 6 : 20),
    );
    return Container(
      constraints: BoxConstraints(maxWidth: maxWidth),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: ShapeDecoration(
        shape: RoundedSuperellipseBorder(borderRadius: radius),
        color: student ? studentColor : teacherColor,
      ),
      child: Text(
        text,
        style: const TextStyle(fontSize: 15, height: 1.55, color: Colors.white),
      ),
    );
  }
}
