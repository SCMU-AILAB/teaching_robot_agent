// frontend/lib/core/config/device_discovery_web.dart
import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'device_discovery_core.dart';

export 'device_discovery_core.dart';

/// Web 端的取数实现：浏览器只能发 HTTP，不能开裸 TCP。
Future<Map<String, Object?>?> fetchSystem(Uri uri, Duration timeout) async {
  try {
    final http.Response response = await http.get(uri).timeout(timeout);
    if (response.statusCode != 200) return null;
    final Object? decoded = jsonDecode(utf8.decode(response.bodyBytes));
    return decoded is Map<String, Object?> ? decoded : null;
  } catch (_) {
    return null;
  }
}

/// 校验已知地址：浏览器里同样能验，只走 HTTP。
Future<DeviceProbe?> probeDevice(
  String baseUrl, {
  SystemFetcher? fetch,
  Duration timeout = const Duration(seconds: 2),
}) async {
  final String base = baseUrl.replaceAll(RegExp(r'/+$'), '');
  final Uri? parsed = Uri.tryParse(base);
  if (parsed == null || !parsed.hasAuthority) return null;
  final SystemFetcher fetcher = fetch ?? (Uri uri) => fetchSystem(uri, timeout);
  final Map<String, Object?>? payload = await fetcher(
    Uri.parse('$base/api/v1/system'),
  );
  return parseSystemPayload(base, payload);
}

/// 浏览器不能扫局域网（没有裸 TCP、没有 mDNS）。
///
/// 返回 null 让调用方走「未连接服务器」，并在设置里手填地址或加 `?api=`。
Future<DeviceProbe?> discoverDevice({
  int port = 8090,
  List<String>? hosts,
  List<String>? subnets,
  PortProbe? probe,
  SystemFetcher? fetch,
  List<String> mdnsNames = const <String>[],
  Duration httpTimeout = const Duration(milliseconds: 1500),
}) async => null;
