// frontend/lib/core/constants/app_radius.dart
import 'package:flutter/material.dart';

/// iPhone 风格圆角规范：统一使用大半径连续圆角（squircle）。
///
/// 页面与控件一律引用本规范，不散落硬编码半径；新增控件按语义选档。
abstract final class AppRadius {
  /// 小标签、状态角标等紧凑元素。
  static const double chip = 10;

  /// 按钮、输入框等可交互控件。
  static const double control = 14;

  /// 提示条等扁平横幅。
  static const double notice = 18;

  /// 主内容卡片。
  static const double card = 24;

  /// 场景图片。
  static const double media = 18;

  /// 控件形状：按钮、输入框的 OutlinedBorder。
  static OutlinedBorder controlShape({
    double radius = control,
    BorderSide side = BorderSide.none,
  }) => RoundedSuperellipseBorder(
    borderRadius: BorderRadius.circular(radius),
    side: side,
  );

  /// 容器形状：带背景色的卡片、提示条、角标。
  static ShapeBorder containerShape({
    required double radius,
    BorderSide side = BorderSide.none,
  }) => RoundedSuperellipseBorder(
    borderRadius: BorderRadius.circular(radius),
    side: side,
  );
}
