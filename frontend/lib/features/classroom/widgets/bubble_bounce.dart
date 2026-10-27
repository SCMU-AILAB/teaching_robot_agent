// frontend/lib/features/classroom/widgets/bubble_bounce.dart
import 'package:flutter/material.dart';

/// 落位「弹两下」弹簧动效：过冲 → 回弹 → 再小幅过冲 → 稳定。
///
/// 整条气泡一起缩放；[play] 从 false 变 true 时播放一次，
/// 用于学生气泡飞入落位后、老师气泡弹出入场时触发。
class BubbleBounce extends StatefulWidget {
  const BubbleBounce({
    required this.child,
    this.play = true,
    this.amplitude = 1,
    this.onDone,
    super.key,
  });

  final Widget child;

  /// 是否播放（false → true 跳变时开始）。
  final bool play;

  /// 弹跳振幅：1 = 发送（弹更欢），0.55 = 接收（更沉稳）。
  /// iMessage 不对称原则：自己发的弹性更大，收到的收敛更稳。
  final double amplitude;

  /// 一次弹跳完成回调。
  final VoidCallback? onDone;

  @override
  State<BubbleBounce> createState() => _BubbleBounceState();
}

class _BubbleBounceState extends State<BubbleBounce>
    with SingleTickerProviderStateMixin {
  // 放缓落位：iMessage 落位约 420–560ms，留出「放置」的从容感。
  static const Duration _duration = Duration(milliseconds: 560);

  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: _duration,
    value: 1,
  )..addStatusListener(_onStatus);

  /// 双段过冲（弹两下），振幅按 [widget.amplitude] 缩放。
  TweenSequence<double> _sequence(double amplitude) => TweenSequence<double>([
    TweenSequenceItem(
      tween: Tween<double>(
        begin: 1,
        end: 1 + 0.12 * amplitude,
      ).chain(CurveTween(curve: Curves.easeOutCubic)),
      weight: 26,
    ),
    TweenSequenceItem(
      tween: Tween<double>(
        begin: 1 + 0.12 * amplitude,
        end: 1 - 0.04 * amplitude,
      ).chain(CurveTween(curve: Curves.easeInOutCubic)),
      weight: 22,
    ),
    // 第二下小幅过冲——「弹两下」的第二弹。
    TweenSequenceItem(
      tween: Tween<double>(
        begin: 1 - 0.04 * amplitude,
        end: 1 + 0.03 * amplitude,
      ).chain(CurveTween(curve: Curves.easeInOutCubic)),
      weight: 22,
    ),
    TweenSequenceItem(
      tween: Tween<double>(
        begin: 1 + 0.03 * amplitude,
        end: 1,
      ).chain(CurveTween(curve: Curves.easeOutCubic)),
      weight: 30,
    ),
  ]);

  @override
  void initState() {
    super.initState();
    if (widget.play) _controller.forward(from: 0);
  }

  @override
  void didUpdateWidget(BubbleBounce oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!oldWidget.play && widget.play) _controller.forward(from: 0);
  }

  void _onStatus(AnimationStatus status) {
    if (status == AnimationStatus.completed) widget.onDone?.call();
  }

  @override
  void dispose() {
    _controller
      ..removeStatusListener(_onStatus)
      ..dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: _controller,
    child: widget.child,
    builder: (BuildContext context, Widget? child) {
      if (!_controller.isAnimating) return child!;
      return Transform.scale(
        scale: _sequence(widget.amplitude).transform(_controller.value),
        filterQuality: FilterQuality.medium,
        child: child,
      );
    },
  );
}
