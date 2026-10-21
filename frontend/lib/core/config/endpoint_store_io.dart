// frontend/lib/core/config/endpoint_store_io.dart
import 'dart:convert';
import 'dart:io';

/// 配置文件位置：macOS 用 Application Support，其它桌面用 XDG 配置目录。
File _configFile() {
  final Map<String, String> env = Platform.environment;
  final String home = env['HOME'] ?? '.';
  final String dir = Platform.isMacOS
      ? '$home/Library/Application Support/TeachingRobot'
      : '${env['XDG_CONFIG_HOME'] ?? '$home/.config'}/teaching_robot';
  return File('$dir/config.json');
}

Future<String?> loadEndpoint() async {
  try {
    final File file = _configFile();
    if (!await file.exists()) return null;
    final Object? decoded = jsonDecode(await file.readAsString());
    if (decoded is Map<String, dynamic>) {
      final String? value = decoded['device_api_base']?.toString();
      return value == null || value.isEmpty ? null : value;
    }
  } catch (_) {
    // 配置损坏不影响启动，按未配置处理。
  }
  return null;
}

Future<void> saveEndpoint(String baseUrl) async {
  try {
    final File file = _configFile();
    await file.parent.create(recursive: true);
    await file.writeAsString(jsonEncode(<String, String>{
      'device_api_base': baseUrl,
    }));
  } catch (_) {
    // 写不进去时本次会话仍生效，下次启动回落到默认地址。
  }
}

Future<void> clearEndpoint() async {
  try {
    final File file = _configFile();
    if (await file.exists()) await file.delete();
  } catch (_) {
    // 删除失败无需打断会话。
  }
}
