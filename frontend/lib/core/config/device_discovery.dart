// frontend/lib/core/config/device_discovery.dart
//
// 平台入口：桌面端可以做完整局域网发现（mDNS + 扫 /24 + TCP 探测），
// 浏览器做不到（没有裸 TCP 与 mDNS），Web 分支只保留单地址校验。
export 'device_discovery_io.dart'
    if (dart.library.js_interop) 'device_discovery_web.dart';
