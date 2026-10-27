// frontend/lib/features/robot_console/theme/console_theme.dart
import 'package:flutter/material.dart';

/// 机器人控制台设计变量：浅色现代工作台配色。
abstract final class ConsoleColors {
  /// 页面背景。
  static const Color background = Color(0xfff6f7f6);

  /// 面板白底。
  static const Color panel = Color(0xffffffff);

  /// 次级面板底色。
  static const Color panelSubtle = Color(0xfff1f4f2);

  /// 面板与描边颜色。
  static const Color border = Color(0xffe2e7e4);

  /// 主要文字颜色。
  static const Color textPrimary = Color(0xff1f2a26);

  /// 次要文字颜色。
  static const Color textSecondary = Color(0xff5c6a64);

  /// 弱提示文字颜色。
  static const Color textMuted = Color(0xff93a19a);

  /// 主色 teal。
  static const Color accent = Color(0xff147d64);

  /// 主色浅底。
  static const Color accentSoft = Color(0xffe3efe9);

  /// 成功状态色。
  static const Color success = Color(0xff2f8f5b);

  /// 成功浅底。
  static const Color successSoft = Color(0xffe2f0e8);

  /// 警告状态色。
  static const Color warning = Color(0xffb97a1f);

  /// 警告浅底。
  static const Color warningSoft = Color(0xfff7ecda);

  /// 错误状态色。
  static const Color error = Color(0xffb6403a);

  /// 错误浅底。
  static const Color errorSoft = Color(0xfff6e5e3);

  /// 信息状态色。
  static const Color info = Color(0xff3a6ea8);

  /// 信息浅底。
  static const Color infoSoft = Color(0xffe4edf7);
}

/// 机器人控制台文字样式。
abstract final class ConsoleText {
  /// 页面标题。
  static const TextStyle pageTitle = TextStyle(
    fontSize: 20,
    fontWeight: FontWeight.w700,
    color: ConsoleColors.textPrimary,
  );

  /// 分区标题。
  static const TextStyle sectionTitle = TextStyle(
    fontSize: 15,
    fontWeight: FontWeight.w600,
    color: ConsoleColors.textPrimary,
  );

  /// 卡片标题。
  static const TextStyle cardTitle = TextStyle(
    fontSize: 13,
    fontWeight: FontWeight.w600,
    color: ConsoleColors.textPrimary,
  );

  /// 正文。
  static const TextStyle body = TextStyle(
    fontSize: 14,
    height: 1.6,
    color: ConsoleColors.textPrimary,
  );

  /// 小号正文。
  static const TextStyle bodySmall = TextStyle(
    fontSize: 13,
    height: 1.5,
    color: ConsoleColors.textSecondary,
  );

  /// 弱提示。
  static const TextStyle caption = TextStyle(
    fontSize: 12,
    color: ConsoleColors.textMuted,
  );

  /// 状态标签。
  static const TextStyle chipLabel = TextStyle(
    fontSize: 11,
    fontWeight: FontWeight.w600,
  );
}

/// 机器人控制台间距与尺寸。
abstract final class ConsoleSpace {
  /// 页面内边距。
  static const double pagePadding = 24;

  /// 面板间距。
  static const double panelGap = 16;

  /// 卡片内边距。
  static const double cardPadding = 16;

  /// 列表项间距。
  static const double itemGap = 8;

  /// 面板圆角。
  static const double radius = 10;

  /// 小圆角（按钮、输入框）。
  static const double radiusSmall = 6;

  /// 按钮高度。
  static const double buttonHeight = 40;

  /// 输入框高度。
  static const double inputHeight = 44;
}

/// 标准面板装饰：白底（或指定底色）、1px 边框、圆角与极浅阴影。
BoxDecoration consolePanelDecoration({Color? color}) => BoxDecoration(
  color: color ?? ConsoleColors.panel,
  border: Border.all(color: ConsoleColors.border),
  borderRadius: BorderRadius.circular(ConsoleSpace.radius),
  boxShadow: const [
    BoxShadow(color: Color(0x0a000000), blurRadius: 8, offset: Offset(0, 2)),
  ],
);

/// 构建机器人控制台的 Material 3 主题。
ThemeData buildConsoleTheme() => ThemeData(
  useMaterial3: true,
  fontFamily: 'NotoSansSC',
  scaffoldBackgroundColor: ConsoleColors.background,
  colorScheme: ColorScheme.fromSeed(
    seedColor: ConsoleColors.accent,
    surface: ConsoleColors.panel,
  ),
  inputDecorationTheme: InputDecorationTheme(
    filled: true,
    fillColor: ConsoleColors.panel,
    border: OutlineInputBorder(
      borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
      borderSide: const BorderSide(color: ConsoleColors.border),
    ),
    enabledBorder: OutlineInputBorder(
      borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
      borderSide: const BorderSide(color: ConsoleColors.border),
    ),
    contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
  ),
  filledButtonTheme: FilledButtonThemeData(
    style: FilledButton.styleFrom(
      backgroundColor: ConsoleColors.accent,
      foregroundColor: ConsoleColors.panel,
      minimumSize: const Size(64, ConsoleSpace.buttonHeight),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
      ),
    ),
  ),
  outlinedButtonTheme: OutlinedButtonThemeData(
    style: OutlinedButton.styleFrom(
      foregroundColor: ConsoleColors.accent,
      minimumSize: const Size(64, ConsoleSpace.buttonHeight),
      side: const BorderSide(color: ConsoleColors.border),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
      ),
    ),
  ),
  textButtonTheme: TextButtonThemeData(
    style: TextButton.styleFrom(
      foregroundColor: ConsoleColors.accent,
      minimumSize: const Size(64, ConsoleSpace.buttonHeight),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
      ),
    ),
  ),
  dividerTheme: const DividerThemeData(
    color: ConsoleColors.border,
    thickness: 1,
    space: 1,
  ),
);
