// frontend/lib/features/classroom/widgets/bubble_send_transition.dart
import 'package:flutter/material.dart';

import 'bubble_bounce.dart';
import 'message_bubble.dart';

/// 发送形变：输入框整体收缩、变蓝，再飞入对话落点并「弹两下」。
///
/// 幽灵画在根 Overlay 上，永远盖在输入框图层之上；两阶段：
/// 1. 收缩段：输入框整框向中心收缩到气泡大小，同时白底→iMessage 蓝、
///    描边并入蓝底、文字深色→白——「整个框在变小的同时变蓝」；
/// 2. 飞行段：蓝色小气泡飞向对话列表落点；
/// 3. 落位后真实消息块显示并整块「弹两下」，完成后回调放行老师回答。
class BubbleSendTransition extends StatefulWidget {
  const BubbleSendTransition({
    required this.fromKey,
    required this.text,
    required this.child,
    this.play = true,
    this.onComplete,
    this.morphing,
    this.landingKey,
    super.key,
  });

  /// 创建时是否播放飞入。false = 该消息的入场已消费过（如列表滚动回收后
  /// 重建），直接静态展示，绝不重播。
  final bool play;

  /// 输入框的 GlobalKey，作为形变起点（框体外观）。
  final GlobalKey fromKey;

  /// 发送的文字，幽灵框内显示。
  final String text;

  /// 整条消息块（名字 + 气泡），落位后整块「弹两下」。
  final Widget child;

  /// 弹两下完成后回调，用于放行排队的老师回答。
  final VoidCallback? onComplete;

  /// 落点量测目标（消息块内的气泡本体）；缺省退化为整个消息块。
  ///
  /// 必须量气泡而非整块（名字+气泡）：否则幽灵被拉高，落位瞬间
  /// 真实气泡更矮，会出现上下长度突变。
  final GlobalKey? landingKey;

  /// 形变期间为 true：页面隐藏真实输入框，让「整框变小」可见。
  final ValueNotifier<bool>? morphing;

  @override
  State<BubbleSendTransition> createState() => _BubbleSendTransitionState();
}

class _BubbleSendTransitionState extends State<BubbleSendTransition>
    with SingleTickerProviderStateMixin {
  /// iMessage 全程 <400ms；带「输入框收缩」多一段，整体放缓到 680ms
  /// 从容完成「靠左收缩 → 飞出 → 落位」。
  static const Duration _duration = Duration(milliseconds: 680);

  /// 收缩段占总时长比例，其后为飞行段（放置段更慢，符合「延缓放置」）。
  static const double _shrinkPhase = 0.40;

  /// 落位交叉淡化起点：飞行末段真实块浮现、幽灵淡出，消除落位突变。
  static const double _fadePhase = 0.82;

  /// 在 initState 创建：惰性初始化会让从未播放的实例在 dispose 中才创建 ticker。
  late final AnimationController _controller;

  OverlayEntry? _entry;
  bool _landed = false;

  /// 创建时定格：后续重建即使 play 变 false，也不中断进行中的飞入。
  late bool _animate;

  /// morphing 是否由本实例置位（回收时只复位自己的，避免误伤正在飞的另一条）。
  bool _ownsMorph = false;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: _duration)
      ..addStatusListener(_onStatus);
    _animate = widget.play;
    // 静态路径直接落位：不飞、不回调（放行由兜底计时器负责）。
    _landed = !_animate;
    if (_animate) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _start());
    }
  }

  void _onStatus(AnimationStatus status) {
    if (status == AnimationStatus.completed) _land();
  }

  /// 量测起点/落点并在根 Overlay 插入幽灵；量测失败则直接落位。
  void _start() {
    if (!mounted) return;
    final Rect? from = _rectOf(widget.fromKey);
    final Rect? to = _rectOf(widget.landingKey ?? context);
    final OverlayState? overlay = Overlay.maybeOf(context, rootOverlay: true);
    if (from == null || to == null || overlay == null) {
      _land();
      return;
    }
    widget.morphing?.value = true;
    _ownsMorph = true;
    _entry = OverlayEntry(
      builder: (BuildContext context) => IgnorePointer(
        child: AnimatedBuilder(
          animation: _controller,
          builder: (BuildContext context, _) =>
              _ghost(from, to, _controller.value),
        ),
      ),
    );
    overlay.insert(_entry!);
    _controller.forward();
  }

  /// [target] 可为 GlobalKey 或 State.context，量测其全局矩形。
  Rect? _rectOf(Object target) {
    final BuildContext? ctx = switch (target) {
      final GlobalKey key => key.currentContext,
      final BuildContext ctx => ctx,
      _ => null,
    };
    final RenderObject? object = ctx?.findRenderObject();
    if (object is! RenderBox || !object.attached) return null;
    return object.localToGlobal(Offset.zero) & object.size;
  }

  /// 幽灵帧：靠左收缩+变蓝 → 飞向落点；每帧重测矩形，列表滚动不失准。
  ///
  /// 文字排版宽度全程冻结（与终点气泡同宽），只动画容器矩形与颜色——
  /// iMessage 原则的落地：移动过程中绝不 reflow，一行字永远一行。
  Widget _ghost(Rect fromStatic, Rect toStatic, double raw) {
    final Rect from = _rectOf(widget.fromKey) ?? fromStatic;
    final Rect to = _rectOf(widget.landingKey ?? context) ?? toStatic;

    // 收缩段：输入框整框向左收拢到气泡大小（左边缘锚定，即「缩小到靠左边」）。
    final double shrink = Curves.easeInOutCubic.transform(
      (raw / _shrinkPhase).clamp(0, 1),
    );
    final Rect small = Rect.fromLTWH(
      from.left,
      from.center.dy - to.height / 2,
      to.width,
      to.height,
    );
    final Rect shrunk = Rect.lerp(from, small, shrink)!;

    // 飞行段：从靠左位置飞向落点；easeInOut 收尾更缓，落位从容。
    final double fly = Curves.easeInOutCubic.transform(
      ((raw - _shrinkPhase) / (1 - _shrinkPhase)).clamp(0, 1),
    );
    final Rect rect = Rect.lerp(shrunk, to, fly)!;

    // 末段交叉淡化：真实块浮现、幽灵淡出，落位瞬间不再有可见切换。
    final double land = _landIn(raw);
    return Stack(
      children: [
        Positioned(
          left: rect.left,
          top: rect.top,
          width: rect.width,
          height: rect.height,
          child: Opacity(
            opacity: 1 - land,
            child: RepaintBoundary(
              child: _MorphBox(
                text: widget.text,
                progress: shrink,
                // 文字 wrap 约束 = 输入框内容宽（打字时的排版），
                // 一行输入飞行中永远一行；终点气泡同约束，落位零跳变。
                // 下限保护：量测失效时退化为终点气泡内容宽。
                textWidth: from.width > 56 ? from.width - 28 : to.width - 28,
              ),
            ),
          ),
        ),
      ],
    );
  }

  /// 落位交叉淡化进度：[_fadePhase, 1] 区间线性 0→1。
  double _landIn(double raw) =>
      ((raw - _fadePhase) / (1 - _fadePhase)).clamp(0.0, 1.0);

  /// 落位：幽灵移除，真实消息块显示（由 build 包「弹两下」），输入框恢复。
  void _land() {
    if (!mounted || _landed) return;
    widget.morphing?.value = false;
    setState(() => _landed = true);
    _entry?.remove();
    _entry = null;
  }

  @override
  void dispose() {
    // dispose 发生在锁树阶段，不能同步通知监听者；延到帧末复位，
    // 避免中途被回收后输入框永久隐藏。只复位自己置位的那次。
    if (_ownsMorph) {
      final ValueNotifier<bool>? morphing = widget.morphing;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        morphing?.value = false;
      });
    }
    _entry?.remove();
    _controller
      ..removeStatusListener(_onStatus)
      ..dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // 静态路径：不飞、不弹，重建多少次都只是普通消息块。
    if (!_animate) return widget.child;
    if (!_landed) {
      // 保持布局占位（保布局不跳动）；飞行末段随幽灵淡出而淡入，
      // 落位瞬间零突变。
      return AnimatedBuilder(
        animation: _controller,
        child: widget.child,
        builder: (BuildContext context, Widget? child) =>
            Opacity(opacity: _landIn(_controller.value), child: child),
      );
    }
    // 落位后整条消息块「弹两下」，完成后放行排队回答。
    return BubbleBounce(
      play: true,
      onDone: widget.onComplete,
      child: widget.child,
    );
  }
}

/// 形变框：白底描边输入框外观 → iMessage 蓝气泡外观，随 progress 同步过渡。
///
/// 文字放在定宽 [textWidth] 内（= 终点气泡内容宽），布局只算一次，
/// 容器再怎么缩放文字也不 reflow。
class _MorphBox extends StatelessWidget {
  const _MorphBox({
    required this.text,
    required this.progress,
    required this.textWidth,
  });

  final String text;

  /// 0 = 输入框外观，1 = 蓝气泡外观（收缩段结束即到位）。
  final double progress;

  /// 文字冻结布局宽度（终点气泡内容宽）。
  final double textWidth;

  @override
  Widget build(BuildContext context) {
    final double p = progress.clamp(0.0, 1.0);
    return Container(
      alignment: Alignment.topLeft,
      padding: EdgeInsets.lerp(
        const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
        const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        p,
      ),
      decoration: ShapeDecoration(
        shape: RoundedSuperellipseBorder(
          borderRadius: BorderRadius.lerp(
            BorderRadius.circular(14),
            const BorderRadius.only(
              topLeft: Radius.circular(20),
              topRight: Radius.circular(20),
              bottomLeft: Radius.circular(20),
              bottomRight: Radius.circular(6),
            ),
            p,
          )!,
          side: BorderSide(
            // 描边先并入蓝底再淡出，避免「灰边蓝底」脏中间态。
            color: Color.lerp(
              const Color(0xffdce3e5),
              MessageBubble.studentColor,
              p,
            )!,
            width: 1,
          ),
        ),
        color: Color.lerp(Colors.white, MessageBubble.studentColor, p),
      ),
      // 冻结排版：OverflowBox 挣脱容器宽度约束，文字永远按输入框内容宽
      // 换行（打字时一行 = 飞行中一行）；只缩容器不动文字布局（iMessage 原则）。
      child: OverflowBox(
        alignment: Alignment.topLeft,
        maxWidth: textWidth,
        minWidth: textWidth,
        child: Text(
          text,
          maxLines: 3,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
            fontSize: 15,
            height: 1.55,
            color: Color.lerp(const Color(0xff28323a), Colors.white, p),
          ),
        ),
      ),
    );
  }
}
