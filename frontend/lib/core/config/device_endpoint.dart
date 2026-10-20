// frontend/lib/core/config/device_endpoint.dart

/// 设备服务地址解析，地址不写进业务代码。
///
/// 优先级：
/// 1. 构建期注入 `--dart-define=DEVICE_API_BASE=...`（跨机调试，例如 Mac 上
///    的开发服务器连 Jetson）；
/// 2. 页面同源：页面本身由设备端服务（默认 8090 端口）托管时直接命中；
/// 3. 其余情况（桌面端无同源概念）回退本机端口，连不上即显示未连接。
String resolveDeviceBaseUrl() {
  const String configured = String.fromEnvironment('DEVICE_API_BASE');
  if (configured.isNotEmpty) return configured;
  final Uri base = Uri.base;
  if (base.hasScheme && (base.scheme == 'http' || base.scheme == 'https')) {
    return base.origin;
  }
  return 'http://127.0.0.1:8090';
}
