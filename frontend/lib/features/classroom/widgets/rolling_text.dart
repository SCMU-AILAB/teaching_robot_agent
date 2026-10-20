// frontend/lib/features/classroom/widgets/rolling_text.dart
import 'package:flutter/material.dart';

/// 苹果风格 odometer：只有发生变化的字符竖向滚动，其余字符完全静止。
///
/// 每个字符是一个独立的裁剪窗口，新旧字符整块平移通过窗口：
/// 不做透明度淡出、不做模糊，靠裁剪呈现真实的卷动。
/// 方向可反转（新字从上方进入），用于状态回退。
class RollingText extends StatefulWidget {
  const RollingText(
    this.text, {
    this.style,
    this.forward = true,
    this.duration = const Duration(milliseconds: 320),
    this.stagger = const Duration(milliseconds: 12),
    super.key,
  });

  final String text;
  final TextStyle? style;

  /// true：旧字向上移出、新字从下方进入；false：方向相反。
  final bool forward;

  final Duration duration;

  /// 相邻字符的启动延迟，制造轻微卷轴错落。
  final Duration stagger;

  @override
  State<RollingText> createState() => _RollingTextState();
}

class _RollingTextState extends State<RollingText>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: widget.duration,
    value: 1,
  );
  late List<String> _current = widget.text.characters.toList();
  late List<String> _previous = List<String>.of(_current);
  bool _forward = true;

  @override
  void initState() {
    super.initState();
    _forward = widget.forward;
  }

  @override
  void didUpdateWidget(RollingText oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.text == oldWidget.text) return;
    _previous = List<String>.of(_current);
    _current = widget.text.characters.toList();
    _forward = widget.forward;
    _controller.forward(from: 0);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  /// 按字符下标换算延迟比例，再裁剪到 0–1。
  double _localProgress(double value, int index) {
    final double total = widget.duration.inMicroseconds.toDouble();
    final double delay = (index * widget.stagger.inMicroseconds) / total;
    final double begin = delay.clamp(0.0, 0.6);
    return ((value - begin) / (1 - begin)).clamp(0.0, 1.0);
  }

  @override
  Widget build(BuildContext context) {
    final int length = _current.length > _previous.length
        ? _current.length
        : _previous.length;
    return AnimatedBuilder(
      animation: _controller,
      builder: (BuildContext context, Widget? child) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (int index = 0; index < length; index++)
            _CharRoller(
              key: ValueKey<int>(index),
              previous: index < _previous.length ? _previous[index] : '',
              current: index < _current.length ? _current[index] : '',
              progress: _localProgress(_controller.value, index),
              forward: _forward,
              style: widget.style,
            ),
        ],
      ),
    );
  }
}

/// 单个字符的滚轮窗口：未变化的字符保持静止，变化的字符整块滚过窗口。
class _CharRoller extends StatelessWidget {
  const _CharRoller({
    required this.previous,
    required this.current,
    required this.progress,
    required this.forward,
    required this.style,
    super.key,
  });

  final String previous;
  final String current;
  final double progress;
  final bool forward;
  final TextStyle? style;

  /// iOS 系统级缓动：起步快、收尾长而平滑。
  static const Curve _iosCurve = Cubic(0.32, 0.72, 0, 1);

  static const double _lineHeightFactor = 1.45;

  @override
  Widget build(BuildContext context) {
    final double fontSize = style?.fontSize ?? 14;
    final double cellHeight = fontSize * _lineHeightFactor;
    // 中文是 1em 等宽，直接按字号定宽：窗口宽度恒定，
    // 不依赖字形测量，滚动时不会推动相邻字符。
    final double cellWidth = fontSize;

    // 静止态或动画结束：只保留当前字符，避免旧字符残留在树里。
    if (previous == current || progress >= 1) {
      return SizedBox(
        width: cellWidth,
        height: cellHeight,
        child: Center(child: Text(current, style: style)),
      );
    }

    final double t = _iosCurve.transform(progress);
    // 只做垂直位移：旧字整块移出窗口，新字从另一侧进入。
    final double offset = forward ? -cellHeight * t : cellHeight * t;
    final double previousDy = offset;
    final double currentDy = forward
        ? cellHeight + offset
        : -cellHeight + offset;

    return ClipRect(
      child: SizedBox(
        width: cellWidth,
        height: cellHeight,
        child: Stack(
          children: [
            Positioned.fill(
              child: Transform.translate(
                offset: Offset(0, previousDy),
                child: Center(child: Text(previous, style: style)),
              ),
            ),
            Positioned.fill(
              child: Transform.translate(
                offset: Offset(0, currentDy),
                child: Center(child: Text(current, style: style)),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
