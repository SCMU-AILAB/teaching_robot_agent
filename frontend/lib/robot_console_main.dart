// frontend/lib/robot_console_main.dart
import 'package:flutter/material.dart';

import 'features/robot_console/pages/robot_console_page.dart';
import 'features/robot_console/theme/console_theme.dart';

/// 机器人控制台独立入口：本地模拟数据，不连接真实机器人。
void main() => runApp(const RobotConsoleApp());

/// 家庭学习陪伴与安全教育机器人控制台应用。
class RobotConsoleApp extends StatelessWidget {
  const RobotConsoleApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: '家庭学习陪伴与安全教育机器人',
    debugShowCheckedModeBanner: false,
    theme: buildConsoleTheme(),
    home: const RobotConsolePage(),
  );
}
