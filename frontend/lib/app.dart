// frontend/lib/app.dart
import 'package:flutter/material.dart';

import 'core/constants/app_radius.dart';
import 'features/classroom/classroom_page.dart';

/// 家庭学习陪伴与安全教育机器人：家长与操作者端入口。
class TeachingRobotApp extends StatelessWidget {
  const TeachingRobotApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: '家庭陪伴机器人',
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
          borderRadius: BorderRadius.circular(AppRadius.control),
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
          shape: AppRadius.controlShape(),
        ),
      ),
    ),
    home: const ClassroomPage(),
  );
}
