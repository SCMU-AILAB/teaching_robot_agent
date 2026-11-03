// frontend/lib/main.dart
import 'package:flutter/material.dart';

import 'app.dart';
import 'core/config/direct_connection.dart';

/// 显式启动页面，不在模块加载时连接设备或服务。
void main() {
  useDirectConnection();
  runApp(const TeachingRobotApp());
}
