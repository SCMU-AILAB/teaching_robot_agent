// frontend/lib/features/classroom/widgets/task_toggle_button.dart
import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

import '../../../core/constants/app_radius.dart';
import 'rolling_text.dart';

/// 「开始课堂 / 停止课堂」二合一按钮。
///
/// 同一个按钮按任务状态切换语义，颜色、图标与文案均做平滑过渡，
/// 避免两个独立按钮并排出现或消失造成的视觉跳变。
class TaskToggleButton extends StatefulWidget {
  const TaskToggleButton({
    required this.active,
    required this.cancelling,
    required this.onStart,
    required this.onStop,
    super.key,
  });

  /// 任务处于 running 或 cancelling。
  final bool active;
  final bool cancelling;
  final VoidCallback? onStart;
  final VoidCallback? onStop;

  @override
  State<TaskToggleButton> createState() => _TaskToggleButtonState();
}

class _TaskToggleButtonState extends State<TaskToggleButton>
    with SingleTickerProviderStateMixin {
  static const Color _accent = Color(0xff147d64);
  static const Color _danger = Color(0xffb6403a);
  static const Color _dangerSurface = Color(0xfffdf0ee);
  static const Color _disabledSurface = Color(0xffe4e9eb);
  static const Color _disabledInk = Color(0xff98a6a0);

  late final AnimationController _morph = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 320),
    value: widget.active ? 1 : 0,
  );

  @override
  void didUpdateWidget(TaskToggleButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.active != oldWidget.active) {
      if (widget.active) {
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

  Widget _icon(Color color) => SizedBox.square(
    dimension: 19,
    child: AnimatedBuilder(
      animation: _morph,
      builder: (BuildContext context, Widget? child) {
        final double t = Curves.easeInOutCubic.transform(_morph.value);
        final double playOut = _phase(t, 0, 0.55);
        final double stopIn = _phase(t, 0.42, 1);
        return Stack(
          alignment: Alignment.center,
          children: [
            // 播放图标先收缩淡出，为停止图标让出画面。
            if (playOut < 1)
              Opacity(
                opacity: 1 - playOut,
                child: Transform.rotate(
                  angle: -0.5 * playOut,
                  child: Transform.scale(
                    scale: 1 - 0.5 * playOut,
                    child: Icon(
                      Symbols.play_arrow_rounded,
                      size: 18,
                      color: color,
                      fill: 1,
                    ),
                  ),
                ),
              ),
            // 停止图标稍后展开淡入，两者只在短窗口内重叠。
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
    final bool startEnabled = !widget.active && widget.onStart != null;
    final bool stopEnabled = widget.active && !widget.cancelling;
    final bool enabled = startEnabled || stopEnabled;
    // 只切换「开始 / 停止」两个动词字，尾部「课堂」保持静止。
    final String verb = widget.active ? '停止' : '开始';
    // 形状与半径恒定，提到动画外只构造一次。
    final ShapeBorder shape = AppRadius.controlShape();

    return AnimatedBuilder(
      animation: _morph,
      builder: (BuildContext context, Widget? child) {
        final double t = Curves.easeInOutCubic.transform(_morph.value);

        // 底色与字色用同一条进度插值，杜绝「绿底红字」的错位中间态。
        Color surface = Color.lerp(_accent, _dangerSurface, t)!;
        Color ink = Color.lerp(Colors.white, _danger, t)!;
        if (!widget.active && widget.onStart == null) {
          // 未连接等真正不可用的空闲态：整体置灰。
          surface = _disabledSurface;
          ink = _disabledInk;
        } else if (widget.cancelling) {
          // 取消确认中：底色保持浅红，仅字色变灰。
          ink = _disabledInk;
        }

        return DecoratedBox(
          decoration: ShapeDecoration(shape: shape, color: surface),
          child: Material(
            color: Colors.transparent,
            shape: shape,
            clipBehavior: Clip.antiAlias,
            child: InkWell(
              // key 必须跨状态保持稳定：一旦随状态变化，Flutter 会销毁并重建
              // 整棵子树，滚动文字的动画状态会随之丢失。
              key: const Key('task-toggle'),
              splashFactory: NoSplash.splashFactory,
              highlightColor: Colors.transparent,
              onTap: !enabled
                  ? null
                  : widget.active
                  ? widget.onStop
                  : widget.onStart,
              child: Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 20,
                  vertical: 18,
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    _icon(ink),
                    const SizedBox(width: 9),
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        RollingText(
                          verb,
                          // 进入运行态向上翻，回到空闲态向下翻。
                          forward: widget.active,
                          style: TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w600,
                            color: ink,
                          ),
                        ),
                        // 与滚轮同高并居中，保证两者基线严格对齐。
                        SizedBox(
                          height: 14 * 1.45,
                          child: Center(
                            child: Text(
                              '课堂',
                              style: TextStyle(
                                fontSize: 14,
                                fontWeight: FontWeight.w600,
                                color: ink,
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}
