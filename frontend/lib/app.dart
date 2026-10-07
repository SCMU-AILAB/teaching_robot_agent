// frontend/lib/app.dart
import 'package:flutter/material.dart';

import 'features/classroom/classroom_page.dart';

/// 教学机器人模拟展示入口。
class TeachingRobotApp extends StatelessWidget {
  const TeachingRobotApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: '教学机器人 · 模拟课堂',
    debugShowCheckedModeBanner: false,
    theme: ThemeData(
      useMaterial3: true,
      fontFamily: 'NotoSansSC',
      colorScheme: ColorScheme.fromSeed(
        seedColor: const Color(0xff147d64),
        surface: Colors.white,
      ),
      scaffoldBackgroundColor: const Color(0xfff5f7f8),
      textTheme: const TextTheme(
        bodyMedium: TextStyle(
          fontSize: 14,
          height: 1.6,
          color: Color(0xff28323a),
          letterSpacing: 0,
        ),
        titleMedium: TextStyle(
          fontSize: 17,
          fontWeight: FontWeight.w600,
          letterSpacing: 0,
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: Colors.white,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: const BorderSide(color: Color(0xffdce3e5)),
        ),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 14,
          vertical: 14,
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 18),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
        ),
      ),
    ),
    home: const ClassroomPage(),
  );
}
