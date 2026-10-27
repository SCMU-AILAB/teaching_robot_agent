// frontend/lib/core/config/endpoint_store.dart
import 'endpoint_store_io.dart'
    if (dart.library.js_interop) 'endpoint_store_web.dart'
    as platform;

/// 设备服务地址的本地持久化。
///
/// 桌面端写到用户配置目录的 `config.json`，Web 端写 localStorage；不引入
/// 原生插件依赖，避免 macOS 端重新需要 CocoaPods/SwiftPM。
abstract final class EndpointStore {
  /// 读取上次保存的地址；未配置或读失败返回 null。
  static Future<String?> load() => platform.loadEndpoint();

  /// 保存地址；写失败静默忽略（本次会话仍然生效）。
  static Future<void> save(String baseUrl) => platform.saveEndpoint(baseUrl);

  /// 清除已保存的地址，回落到构建期默认。
  static Future<void> clear() => platform.clearEndpoint();
}
