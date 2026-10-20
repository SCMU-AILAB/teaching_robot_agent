// frontend/lib/core/config/device_endpoint.dart

/// 设备服务地址解析，地址不写进业务代码。
///
/// 优先级：
/// 1. 页面查询参数 `?api=http://host:port` —— 同一份构建指向不同设备的临时覆盖；
/// 2. 构建期注入 `--dart-define=DEVICE_API_BASE=...`（跨机调试，例如 Mac 上
///    的开发服务器连 Jetson）；
/// 3. 页面同源：页面本身由设备端服务（默认 8090 端口）托管时直接命中；
/// 4. 其余情况（桌面端无同源概念）回退本机端口，连不上即显示未连接。
///
/// [base] 仅用于测试注入页面地址，默认取 `Uri.base`。
String resolveDeviceBaseUrl({Uri? base}) {
  final Uri page = base ?? Uri.base;
  final String override = page.queryParameters['api'] ?? '';
  if (override.startsWith('http://') || override.startsWith('https://')) {
    return override.replaceAll(RegExp(r'/+$'), '');
  }
  const String configured = String.fromEnvironment('DEVICE_API_BASE');
  if (configured.isNotEmpty) return configured;
  if (page.hasScheme && (page.scheme == 'http' || page.scheme == 'https')) {
    return page.origin;
  }
  return 'http://127.0.0.1:8090';
}
