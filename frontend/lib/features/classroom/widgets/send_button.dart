// frontend/lib/features/classroom/widgets/send_button.dart
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

/// 发送按钮：提交后在箭头与持续旋转的加载圆环之间形变过渡。
///
/// 出场时箭头向上滑出按钮、圆环稍后浮现；处理完成时圆环先淡出，
/// 箭头再从按钮下方滑入归位，使箭头相对自身始终朝「前上方」运动。
class SendButton extends StatefulWidget {
  const SendButton({required this.loading, required this.onPressed, super.key});

  /// 系统正在处理上一步输入；期间显示加载圆环且不可点击。
  final bool loading;

  /// 为空表示当前流程不允许发送，显示静态禁用外观。
  final VoidCallback? onPressed;

  @override
  State<SendButton> createState() => _SendButtonState();
}

class _SendButtonState extends State<SendButton> with TickerProviderStateMixin {
  static const Color _accent = Color(0xff147d64);
  static const Color _disabledSurface = Color(0xffe4e9eb);
  static const Color _disabledIcon = Color(0xff98a6a0);

  late final AnimationController _spin = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  );

  /// 0 表示箭头，1 表示加载环；切换时正向/反向播放。
  late final AnimationController _morph = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 420),
    reverseDuration: const Duration(milliseconds: 420),
    value: widget.loading ? 1 : 0,
  );

  @override
  void initState() {
    super.initState();
    if (widget.loading) _spin.repeat();
  }

  @override
  void didUpdateWidget(SendButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.loading == oldWidget.loading) return;
    // 旋转只在处理期间运行，结束即停，避免后台空转。
    if (widget.loading) {
      _spin.repeat();
      _morph.forward();
    } else {
      _spin.stop();
      _morph.reverse();
    }
  }

  @override
  void dispose() {
    _spin.dispose();
    _morph.dispose();
    super.dispose();
  }

  /// 把总进度裁剪到 [begin, end] 区间，用于错开两段的出场顺序。
  static double _phase(double value, double begin, double end) =>
      ((value - begin) / (end - begin)).clamp(0.0, 1.0);

  @override
  Widget build(BuildContext context) {
    final bool enabled = widget.onPressed != null;
    final bool active = widget.loading || enabled;
    final Color surface = active ? _accent : _disabledSurface;
    final Color foreground = active ? Colors.white : _disabledIcon;

    // 自绘按钮而非 IconButton：需要按钮自身裁剪，箭头才能「走出」边界，
    // 而不是飘到按钮外面。
    return Tooltip(
      message: widget.loading
          ? '处理中'
          : enabled
          ? '发送文字'
          : '等待课堂就绪',
      child: Material(
        color: surface,
        shape: const CircleBorder(),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          key: const Key('send-message'),
          onTap: widget.loading ? null : widget.onPressed,
          customBorder: const CircleBorder(),
          splashFactory: NoSplash.splashFactory,
          highlightColor: Colors.transparent,
          child: SizedBox.square(
            dimension: 40,
            child: Center(
              child: SizedBox.square(
                dimension: 19,
                child: AnimatedBuilder(
                  animation: _morph,
                  builder: (BuildContext context, Widget? child) {
                    final double raw = _morph.value;
                    // 圆环错峰：raw 0.45→1 淡入，反向时 1→0.45 先淡出。
                    final double enter = Curves.easeOutCubic.transform(
                      _phase(raw, 0.45, 1),
                    );
                    // 箭头相对自身运动方向始终向上：
                    // 出场（forward）向上移出按钮；回程（reverse）从按钮下方滑入归位。
                    final bool reversing =
                        _morph.status == AnimationStatus.reverse;
                    final double offset;
                    final bool showArrow;
                    if (reversing) {
                      // 回程 raw 1→0：raw≤0.45 段箭头自下方向上归位。
                      // 剩余距离 raw/0.45 用 easeInCubic 收缩，即减速抵达。
                      final double remain = (raw / 0.45).clamp(0.0, 1.0);
                      offset = 24 * Curves.easeInCubic.transform(remain);
                      showArrow = raw <= 0.45;
                    } else {
                      // 出场 raw 0→1：raw<0.55 段箭头向上移出并被按钮圆边裁掉。
                      final double exit = Curves.easeInCubic.transform(
                        _phase(raw, 0, 0.55),
                      );
                      offset = -24 * exit;
                      showArrow = exit < 1;
                    }

                    return Stack(
                      clipBehavior: Clip.none,
                      alignment: Alignment.center,
                      children: [
                        // 位移 24px：足以让箭头完全越过按钮边缘并被裁掉。
                        if (showArrow)
                          Transform.translate(
                            offset: Offset(0, offset),
                            child: Icon(
                              Symbols.arrow_upward_rounded,
                              size: 19,
                              weight: 600,
                              color: foreground,
                            ),
                          ),
                        // 圆环在箭头离场后浮现，随即可见其自身旋转。
                        if (enter > 0)
                          Opacity(
                            opacity: enter,
                            child: Transform.scale(
                              scale: 0.7 + 0.3 * enter,
                              child: _DotSpinner(
                                key: const ValueKey('spinner'),
                                spin: _spin,
                              ),
                            ),
                          ),
                      ],
                    );
                  },
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// 由点阵亮度波驱动的加载指示，整体持续旋转。
class _DotSpinner extends StatelessWidget {
  const _DotSpinner({required this.spin, super.key});

  final Animation<double> spin;

  @override
  Widget build(BuildContext context) => SizedBox.square(
    dimension: 19,
    child: AnimatedBuilder(
      animation: spin,
      builder: (BuildContext context, Widget? child) =>
          CustomPaint(painter: _DotRingPainter(progress: spin.value)),
    ),
  );
}

/// 点阵加载环：12 个圆点沿圆周排布，亮度波依次向后衰减，
/// 形成持续追逐的旋转；相邻点相位连续，避免弧线式的硬转。
class _DotRingPainter extends CustomPainter {
  const _DotRingPainter({required this.progress});

  final double progress;

  static const int _dotCount = 12;
  static const double _dotRadius = 1.12;

  @override
  void paint(Canvas canvas, Size size) {
    final Offset center = size.center(Offset.zero);
    final double orbit = size.shortestSide / 2 - _dotRadius;
    final Paint paint = Paint();

    for (int index = 0; index < _dotCount; index++) {
      // 相邻点依次落后一个相位，形成彗尾式亮度梯度。
      final double phase = (progress - index / _dotCount) % 1.0;
      final double intensity = math.pow(phase, 2.2).toDouble();
      final double angle = -math.pi / 2 + index / _dotCount * math.pi * 2;
      final Offset dot =
          center + Offset(math.cos(angle), math.sin(angle)) * orbit;

      // 亮度与半径同步变化，头部更亮更大，尾部淡入淡出无跳变。
      paint.color = Colors.white.withValues(alpha: 0.10 + 0.90 * intensity);
      canvas.drawCircle(dot, _dotRadius * (0.74 + 0.26 * intensity), paint);
    }
  }

  @override
  bool shouldRepaint(_DotRingPainter oldDelegate) =>
      oldDelegate.progress != progress;
}
