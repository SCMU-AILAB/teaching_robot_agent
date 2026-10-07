// frontend/lib/features/classroom/widgets/conversation_panel.dart
import 'package:flutter/material.dart';

import '../session_model.dart';
import 'status_label.dart';

/// 展示完整对话及独立播放状态，长文本可滚动阅读。
class ConversationPanel extends StatefulWidget {
  const ConversationPanel({required this.state, super.key});
  final SessionModel state;

  @override
  State<ConversationPanel> createState() => _ConversationPanelState();
}

class _ConversationPanelState extends State<ConversationPanel> {
  final ScrollController _scroll = ScrollController();

  @override
  void didUpdateWidget(ConversationPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.state.messages.length != oldWidget.state.messages.length) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _scroll.hasClients) {
          _scroll.animateTo(
            _scroll.position.maxScrollExtent,
            duration: const Duration(milliseconds: 250),
            curve: Curves.easeOut,
          );
        }
      });
    }
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Row(
        children: [
          const Icon(Icons.forum_outlined, size: 20),
          const SizedBox(width: 9),
          Text('课堂对话', style: Theme.of(context).textTheme.titleMedium),
          const Spacer(),
          StatusLabel(widget.state.stage),
        ],
      ),
      const SizedBox(height: 16),
      SizedBox(
        height: 345,
        child: widget.state.messages.isEmpty
            ? const Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.chat_bubble_outline,
                      size: 40,
                      color: Color(0xffa5b7b2),
                    ),
                    SizedBox(height: 16),
                    Text(
                      '准备好，一起探索吧',
                      style: TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    SizedBox(height: 5),
                    Text(
                      '开始课堂后，这里会显示老师的回答。',
                      style: TextStyle(color: Color(0xff75808a)),
                    ),
                  ],
                ),
              )
            : ListView.separated(
                controller: _scroll,
                itemCount: widget.state.messages.length,
                separatorBuilder: (_, _) => const SizedBox(height: 24),
                itemBuilder: (BuildContext context, int index) {
                  final ClassMessage message = widget.state.messages[index];
                  final bool student = message.role == 'student';
                  return Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      CircleAvatar(
                        radius: 16,
                        backgroundColor: student
                            ? const Color(0xffeaf0fa)
                            : const Color(0xffe5f2ec),
                        child: Icon(
                          student
                              ? Icons.person_outline
                              : Icons.smart_toy_outlined,
                          size: 19,
                          color: student
                              ? const Color(0xff4465a0)
                              : const Color(0xff147d64),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              student ? '你' : '机器人老师',
                              style: const TextStyle(
                                fontSize: 12,
                                color: Color(0xff748079),
                              ),
                            ),
                            const SizedBox(height: 5),
                            SelectableText(
                              message.text,
                              style: const TextStyle(fontSize: 15, height: 1.8),
                            ),
                          ],
                        ),
                      ),
                    ],
                  );
                },
              ),
      ),
      const Divider(height: 28),
      Row(
        children: [
          const Icon(
            Icons.volume_up_outlined,
            size: 18,
            color: Color(0xff73817b),
          ),
          const SizedBox(width: 8),
          const Text('回答播报', style: TextStyle(fontSize: 12)),
          const Spacer(),
          StatusLabel(widget.state.playback),
        ],
      ),
    ],
  );
}
