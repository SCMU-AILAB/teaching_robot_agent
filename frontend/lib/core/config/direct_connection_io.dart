// frontend/lib/core/config/direct_connection_io.dart
import 'dart:io';

/// 本应用的所有 HTTP 请求（接口、事件流、摄像头图片）都直连设备。
///
/// 机器人后端只在局域网开放；从带代理环境变量的终端启动或开了系统代理时，
/// 请求被转给代理，代理访问不到局域网地址，返回 503。
void useDirectConnection() {
  HttpOverrides.global = _DirectHttpOverrides();
}

class _DirectHttpOverrides extends HttpOverrides {
  @override
  HttpClient createHttpClient(SecurityContext? context) =>
      super.createHttpClient(context)..findProxy = (_) => 'DIRECT';
}
