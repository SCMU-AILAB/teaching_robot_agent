// frontend/lib/features/classroom/widgets/record_toggle_button.dart
import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

import 'rolling_text.dart';

/// 「开始录音 / 结束录音」二合一按钮。
///
/// 与 TaskToggleButton 同一套过渡语言：
/// - 只有动词「开始 ↔ 结束」逐字卷动，尾部「录音」保持静止；
/// - 麦克风与停止图标错峰形变（麦克风收缩转出、停止展开进入）；
/// - 边框/字色/底色沿同一条进度插值，录音态渐入红色。
class RecordToggleButton extends StatefulWidget {
  const RecordToggleButton({
    required this.recording,
    required this.onPressed,
    super.key,
  });

  /// 是否正在录音。
  final bool recording;

  /// 为空表示当前不可用，显示禁用外观。
  final VoidCallback? onPressed;

  @override
  State<RecordToggleButton> createState() => _RecordToggleButtonState();
}

class _RecordToggleButtonState extends State<RecordToggleButton>
    with SingleTickerProviderStateMixin {
  static const Color _ink = Color(0xff28323a);
  static const Color _border = Color(0xffdce3e5);
  static const Color _danger = Color(0xffb6403a);
  static const Color _dangerSurface = Color(0xfffdf0ee);

  late final AnimationController _morph = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 320),
    value: widget.recording ? 1 : 0,
  );

  @override
  void didUpdateWidget(RecordToggleButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.recording != oldWidget.recording) {
      if (widget.recording) {
        _morph.forward();
      } else {
        _morph.reverse();
      }
    }
  }

  @override
  void dispose() {
    _morph.dispose();
    super.dispose();
  }

  /// 把总进度裁剪到 [begin, end] 区间，用于让两个图标错峰出现。
  static double _phase(double value, double begin, double end) =>
      ((value - begin) / (end - begin)).clamp(0.0, 1.0);

  /// 麦克风收缩转出、停止图标稍后展开进入。
  Widget _icon(Color color) => SizedBox.square(
    dimension: 19,
    child: AnimatedBuilder(
      animation: _morph,
      builder: (BuildContext context, Widget? child) {
        final double t = Curves.easeInOutCubic.transform(_morph.value);
        final double micOut = _phase(t, 0, 0.55);
        final double stopIn = _phase(t, 0.42, 1);
        return Stack(
          alignment: Alignment.center,
          children: [
            if (micOut < 1)
              Opacity(
                opacity: 1 - micOut,
                child: Transform.rotate(
                  angle: -0.5 * micOut,
                  child: Transform.scale(
                    scale: 1 - 0.5 * micOut,
                    child: Icon(
                      Symbols.mic_none_rounded,
                      size: 19,
                      color: color,
                      fill: 0,
                      weight: 500,
                    ),
                  ),
                ),
              ),
            if (stopIn > 0)
              Opacity(
                opacity: stopIn,
                child: Transform.rotate(
                  angle: 0.5 * (1 - stopIn),
                  child: Transform.scale(
                    scale: 0.55 + 0.45 * stopIn,
                    child: Icon(
                      Symbols.stop_rounded,
                      size: 18,
                      color: color,
                      fill: 1,
                    ),
                  ),
                ),
              ),
          ],
        );
      },
    ),
  );

  @override
  Widget build(BuildContext context) {
    // 动词逐字卷动（只有「开始 ↔ 结束」两字动），「录音」静止。
    final String verb = widget.recording ? '结束' : '开始';

    return AnimatedBuilder(
      animation: _morph,
      builder: (BuildContext context, Widget? child) {
        final double t = Curves.easeInOutCubic.transform(_morph.value);
        // 边框/字色/底色同一条进度插值，杜绝过渡中的错位中间态。
        final Color ink = Color.lerp(_ink, _danger, t)!;
        final Color side = Color.lerp(_border, _danger, t)!;
        final Color surface = Color.lerp(Colors.white, _dangerSurface, t)!;

        return OutlinedButton.icon(
          // key 必须跨状态保持稳定：随状态变化会销毁重建子树，
          // 逐字滚动的动画状态会丢失（同 TaskToggleButton 的教训）。
          key: const Key('record-button'),
          onPressed: widget.onPressed,
          style: OutlinedButton.styleFrom(
            foregroundColor: ink,
            backgroundColor: surface,
            side: BorderSide(color: side),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          ),
          icon: _icon(ink),
          label: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              // 进入录音态向上翻，回到空闲态向下翻。
              RollingText(
                verb,
                forward: widget.recording,
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w500,
                  color: ink,
                ),
              ),
              Text(
                '录音',
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w500,
                  color: ink,
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}
