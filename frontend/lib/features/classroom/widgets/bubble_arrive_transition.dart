// frontend/lib/features/classroom/widgets/bubble_arrive_transition.dart
import 'package:flutter/material.dart';

import 'bubble_bounce.dart';

/// 回复入场：淡入 + 上浮 + 整块「弹两下」，参考 iMessage 新消息弹出。
///
/// 仅在首次插入时播放一次（父级 ValueKey 保证），后续重建不重播。
class BubbleArriveTransition extends StatefulWidget {
  const BubbleArriveTransition({
    required this.child,
    this.play = true,
    super.key,
  });

  /// 创建时是否播放入场。false = 入场已消费过（如列表滚动回收后重建），
  /// 直接静态展示，绝不重播。
  final bool play;

  /// 整条消息块（名字 + 气泡），整块一起弹跳。
  final Widget child;

  @override
  State<BubbleArriveTransition> createState() => _BubbleArriveTransitionState();
}

class _BubbleArriveTransitionState extends State<BubbleArriveTransition>
    with SingleTickerProviderStateMixin {
  static const Duration _duration = Duration(milliseconds: 300);

  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: _duration,
  );

  /// 创建时定格：重建不重播（滚动回收后 play 为 false）。
  late bool _animate;

  @override
  void initState() {
    super.initState();
    _animate = widget.play;
    if (_animate) {
      _controller.forward();
    } else {
      _controller.value = 1;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // 静态路径：重建多少次都只是普通消息块。
    if (!_animate) return widget.child;
    return BubbleBounce(
      // 入场淡入结束后接力「弹两下」，整体是「浮上来再弹两下」。
      // 振幅 0.55：iMessage 不对称原则——收到的消息收敛更稳。
      play: true,
      amplitude: 0.55,
      child: FadeTransition(
        opacity: CurvedAnimation(
          parent: _controller,
          curve: const Interval(0, 0.6, curve: Curves.easeOut),
        ),
        child: AnimatedBuilder(
          animation: _controller,
          child: widget.child,
          builder: (BuildContext context, Widget? child) => Transform.translate(
            offset: Offset(
              0,
              12 * (1 - Curves.easeOutCubic.transform(_controller.value)),
            ),
            child: child,
          ),
        ),
      ),
    );
  }
}
