// frontend/lib/features/classroom/widgets/field_error_box.dart
import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

import '../../../core/constants/app_radius.dart';

/// 紧贴出错控件的小型错误框。
///
/// 出现与消失均为平滑动画：高度展开/收起配合内容淡入淡出与轻微纵向位移，
/// 缩回时高度先收拢，避免瞬间抽走导致下方内容跳变。
/// message 为 null 或空串均视为无错误。
class FieldErrorBox extends StatefulWidget {
  const FieldErrorBox({required this.message, this.action, super.key});

  /// 为空（null 或空串）表示无错误，小框平滑收起。
  final String? message;

  /// 可选操作（如「重新连接」），放在小框右端。
  final Widget? action;

  @override
  State<FieldErrorBox> createState() => _FieldErrorBoxState();
}

class _FieldErrorBoxState extends State<FieldErrorBox>
    with SingleTickerProviderStateMixin {
  static const Duration _duration = Duration(milliseconds: 240);

  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: _duration,
    reverseDuration: const Duration(milliseconds: 200),
  );

  late final CurvedAnimation _height = CurvedAnimation(
    parent: _controller,
    curve: Curves.easeOutCubic,
    // 线性回退：收起中途高度严格单调收缩，避免 easeIn 前段几乎不动。
    reverseCurve: Curves.linear,
  );

  /// 展开/收起都是渐变的（含收起末段），不用分段区间。
  late final CurvedAnimation _fade = CurvedAnimation(
    parent: _controller,
    curve: Curves.easeOut,
    reverseCurve: Curves.easeIn,
  );

  /// 收起期间继续显示的旧文案，避免文字先消失、框再收缩。
  String _text = '';

  bool get _hasError => (widget.message ?? '').isNotEmpty;

  @override
  void initState() {
    super.initState();
    _controller.addStatusListener(_onStatus);
    if (_hasError) {
      _text = widget.message!;
      _controller.value = 1;
    }
  }

  /// 收起完成后重建一次，把已收拢的子树从树上移除。
  void _onStatus(AnimationStatus status) {
    if (status == AnimationStatus.dismissed && mounted) setState(() {});
  }

  @override
  void didUpdateWidget(FieldErrorBox oldWidget) {
    super.didUpdateWidget(oldWidget);
    final bool was = (oldWidget.message ?? '').isNotEmpty;
    final bool now = _hasError;
    if (now) _text = widget.message!;
    if (was == now) return;
    if (now) {
      _controller.forward();
    } else {
      _controller.reverse();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_hasError && _controller.isDismissed) {
      return const SizedBox(width: double.infinity);
    }
    return AnimatedBuilder(
      animation: _controller,
      builder: (BuildContext context, Widget? child) => SizeTransition(
        sizeFactor: _height,
        axis: Axis.vertical,
        alignment: Alignment.topCenter,
        child: FadeTransition(
          opacity: _fade,
          child: Transform.translate(
            // 轻微纵向位移：弹出向下沉一点，缩回向上收，和高度同步。
            offset: Offset(0, 6 * (1 - _height.value)),
            child: child,
          ),
        ),
      ),
      // key 稳定：动画期间子树不重建，文案收起时保留旧文本一起淡出。
      child: Container(
        key: const Key('field-error-box'),
        width: double.infinity,
        margin: const EdgeInsets.only(top: 8),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        decoration: ShapeDecoration(
          shape: AppRadius.containerShape(radius: AppRadius.chip),
          color: const Color(0xffffefed),
        ),
        child: Row(
          children: [
            const Icon(
              Symbols.error_rounded,
              size: 16,
              fill: 1,
              color: Color(0xffb6403a),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                _text,
                style: const TextStyle(
                  fontSize: 12.5,
                  height: 1.35,
                  color: Color(0xff8f2f2a),
                ),
              ),
            ),
            if (widget.action != null) widget.action!,
          ],
        ),
      ),
    );
  }
}
