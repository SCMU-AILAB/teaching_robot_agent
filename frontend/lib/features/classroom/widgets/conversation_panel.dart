// frontend/lib/features/classroom/widgets/conversation_panel.dart
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

import '../session_model.dart';
import 'bubble_arrive_transition.dart';
import 'bubble_send_transition.dart';
import 'message_bubble.dart';
import 'status_label.dart';

/// 展示完整对话及独立播放状态，长文本可滚动阅读。
///
/// 消息按 iMessage 布局：老师在左（主题绿），学生在右（iMessage 蓝）。
/// 学生消息从输入框飞入；期间到达的回答先排队，飞入完成后再弹出，
/// 保证「我的话先说完，老师再开口」的时序。
class ConversationPanel extends StatefulWidget {
  const ConversationPanel({
    required this.state,
    required this.messageInputKey,
    this.sendMorphing,
    super.key,
  });
  final SessionModel state;

  /// 输入框 GlobalKey，作为发送飞入动画的起点。
  final GlobalKey messageInputKey;

  /// 发送形变进行中（页面据此隐藏真实输入框）。
  final ValueNotifier<bool>? sendMorphing;

  @override
  State<ConversationPanel> createState() => _ConversationPanelState();
}

class _ConversationPanelState extends State<ConversationPanel> {
  final ScrollController _scroll = ScrollController();

  /// 输入框内容宽（打字时的文字排版宽度）；学生气泡与飞行幽灵共用同一约束。
  double _inputContentWidth = 272;

  /// 已显示的消息 id。
  final Set<int> _revealed = <int>{};

  /// 等待学生飞入完成后再显示的回答 id。
  final Set<int> _queued = <int>{};

  /// 是否有学生消息正在飞入。
  bool _flying = false;

  /// 每条消息气泡的量测 key：发送飞入的落点取气泡本体而非整块，
  /// 避免落位时名字行撑高幽灵造成上下长度突变。
  final Map<int, GlobalKey> _bubbleKeys = <int, GlobalKey>{};

  /// 入场动画已消费的消息 id（含初始/快照旧消息）。
  ///
  /// ListView 滚动会回收移出视口的 item，重建时 State 重置——若不记账，
  /// 发送飞入/回答弹出会在每次滚回来时重播。此集合保证每条消息入场只播一次。
  final Set<int> _animated = <int>{};

  /// 飞入兜底计时器：防止气泡未建出时回答一直不放行。
  Timer? _releaseTimer;

  static const Duration _flightFallback = Duration(milliseconds: 1400);

  @override
  void initState() {
    super.initState();
    // 页面挂载前已有的消息（快照/重连）不播飞入，避免历史消息齐飞。
    _animated.addAll(widget.state.messages.map((ClassMessage m) => m.id));
    _sync(initial: true);
    WidgetsBinding.instance.addPostFrameCallback((_) => _measureInput());
  }

  /// 量测输入框内容宽，作为学生气泡/幽灵共享的换行约束。
  void _measureInput() {
    if (!mounted) return;
    final RenderObject? object = widget.messageInputKey.currentContext
        ?.findRenderObject();
    if (object is RenderBox && object.attached && object.size.width > 0) {
      setState(() => _inputContentWidth = object.size.width - 28);
    }
  }

  @override
  void didUpdateWidget(ConversationPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    _sync(initial: false);
  }

  /// 同步消息可见性：学生消息立即显示并起飞，回答在飞入期间先排队。
  void _sync({required bool initial}) {
    final List<ClassMessage> messages = widget.state.messages;
    final Set<int> alive = messages
        .map((ClassMessage message) => message.id)
        .toSet();
    final int before = _revealed.length + _queued.length;
    _revealed.removeWhere((int id) => !alive.contains(id));
    _queued.removeWhere((int id) => !alive.contains(id));
    bool changed = _revealed.length + _queued.length != before;

    for (final ClassMessage message in messages) {
      if (_revealed.contains(message.id) || _queued.contains(message.id)) {
        continue;
      }
      changed = true;
      if (message.role == 'student' && !_animated.contains(message.id)) {
        _revealed.add(message.id);
        _flying = true;
        // 兜底：即使气泡未建出（懒加载/量测失败回调丢失），也在飞入时长后放行。
        _releaseTimer?.cancel();
        _releaseTimer = Timer(_flightFallback, _release);
      } else if (message.role != 'student' && _flying && !initial) {
        _queued.add(message.id);
      } else {
        _revealed.add(message.id);
      }
    }

    if (changed && !initial) {
      setState(() {});
      _scrollToBottom();
    }
  }

  /// 学生飞入完成（或兜底超时）：放行排队的回答。
  void _release() {
    if (!mounted) return;
    _releaseTimer?.cancel();
    if (!_flying && _queued.isEmpty) return;
    setState(() {
      _flying = false;
      _revealed.addAll(_queued);
      _queued.clear();
    });
    _scrollToBottom();
  }

  void _scrollToBottom() {
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

  @override
  void dispose() {
    _releaseTimer?.cancel();
    _scroll.dispose();
    super.dispose();
  }

  List<ClassMessage> get _visibleMessages => widget.state.messages
      .where((ClassMessage message) => _revealed.contains(message.id))
      .toList();

  @override
  Widget build(BuildContext context) {
    final List<ClassMessage> messages = _visibleMessages;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const Icon(Symbols.forum_rounded, size: 20),
            const SizedBox(width: 9),
            Text('课堂对话', style: Theme.of(context).textTheme.titleMedium),
            const Spacer(),
            StatusLabel(widget.state.stage),
          ],
        ),
        const SizedBox(height: 16),
        SizedBox(
          height: 345,
          child: messages.isEmpty
              ? const Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        Symbols.chat_bubble_rounded,
                        size: 44,
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
                  itemCount: messages.length,
                  separatorBuilder: (_, _) => const SizedBox(height: 24),
                  itemBuilder: (BuildContext context, int index) {
                    final ClassMessage message = messages[index];
                    final bool student = message.role == 'student';
                    final GlobalKey bubbleKey = _bubbleKeys.putIfAbsent(
                      message.id,
                      GlobalKey.new,
                    );
                    // 整条消息块（名字 + 气泡）一起进入弹跳动效。
                    final Widget block = Column(
                      crossAxisAlignment: student
                          ? CrossAxisAlignment.end
                          : CrossAxisAlignment.start,
                      children: [
                        Text(
                          student ? '你' : '机器人老师',
                          style: const TextStyle(
                            fontSize: 12,
                            color: Color(0xff748079),
                          ),
                        ),
                        const SizedBox(height: 5),
                        MessageBubble(
                          key: student ? bubbleKey : null,
                          text: message.text,
                          student: student,
                          // 学生气泡 wrap = 输入框内容宽：与打字排版一致，
                          // 飞行中/落位后都不 reflow（iMessage 只动位移与缩放）。
                          maxWidth: student ? _inputContentWidth + 28 : 300,
                        ),
                      ],
                    );
                    // 入场只消费一次：Set.add 返回「此前不存在」，正好作为 play。
                    final bool play = _animated.add(message.id);
                    // 学生消息整体向左上收，避免贴右缘/下缘被窗口压住文字；
                    // 只动位移不动布局（iMessage 只动位移与缩放）。
                    return Transform.translate(
                      offset: student ? const Offset(-28, -10) : Offset.zero,
                      child: Row(
                        key: ValueKey('row-${message.id}'),
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisAlignment: student
                            ? MainAxisAlignment.end
                            : MainAxisAlignment.start,
                        children: [
                          if (!student) ...[
                            const CircleAvatar(
                              radius: 16,
                              backgroundColor: Color(0xffe5f2ec),
                              child: Icon(
                                Symbols.smart_toy_rounded,
                                size: 18,
                                color: Color(0xff147d64),
                                fill: 1,
                              ),
                            ),
                            const SizedBox(width: 12),
                          ],
                          Flexible(
                            child: student
                                // 学生消息：输入框收缩变蓝飞入，落位后整块弹两下。
                                // play=false（回收重建）→ 静态展示，绝不重播。
                                ? BubbleSendTransition(
                                    key: ValueKey('send-${message.id}'),
                                    fromKey: widget.messageInputKey,
                                    landingKey: bubbleKey,
                                    text: message.text,
                                    morphing: widget.sendMorphing,
                                    play: play,
                                    onComplete: _release,
                                    child: block,
                                  )
                                // 老师回答：浮入 + 整块弹两下，仅首次插入播放。
                                : BubbleArriveTransition(
                                    key: ValueKey('arrive-${message.id}'),
                                    play: play,
                                    child: block,
                                  ),
                          ),
                        ],
                      ),
                    );
                  },
                ),
        ),
        const Divider(height: 28),
        Row(
          children: [
            const Icon(
              Symbols.volume_up_rounded,
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
}
