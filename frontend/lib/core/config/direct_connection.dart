// frontend/lib/core/config/direct_connection.dart
//
// 平台入口：桌面与移动端连机器人不走代理；浏览器的代理由浏览器决定，Web 分支不做处理。
export 'direct_connection_io.dart'
    if (dart.library.js_interop) 'direct_connection_web.dart';
