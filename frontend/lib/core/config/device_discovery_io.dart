// frontend/lib/core/config/device_discovery_io.dart
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'device_discovery_core.dart';

export 'device_discovery_core.dart';

/// 默认实现：单次 GET，超时或网络异常都按「不是设备端」处理。
Future<Map<String, Object?>?> fetchSystem(Uri uri, Duration timeout) async {
  final HttpClient client = HttpClient()
    ..connectionTimeout = timeout
    ..idleTimeout = const Duration(seconds: 1);
  try {
    final HttpClientRequest request = await client.getUrl(uri).timeout(timeout);
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    final HttpClientResponse response = await request.close().timeout(timeout);
    if (response.statusCode != HttpStatus.ok) {
      await response.drain<void>();
      return null;
    }
    final String body = await response
        .transform(utf8.decoder)
        .join()
        .timeout(timeout);
    final Object? decoded = jsonDecode(body);
    return decoded is Map<String, Object?> ? decoded : null;
  } catch (_) {
    return null;
  } finally {
    client.close(force: true);
  }
}

/// 默认实现：TCP 连接测试，握手成功即关闭。
Future<bool> probePort(String host, int port) async {
  try {
    final Socket socket = await Socket.connect(
      host,
      port,
      timeout: const Duration(milliseconds: 260),
    );
    socket.destroy();
    return true;
  } catch (_) {
    return false;
  }
}

/// 校验一个已知地址是不是设备端服务。
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

/// 本机所有可扫的网段前缀（去重，最多 3 个）。
Future<List<String>> localSubnets() async {
  final Set<String> bases = <String>{};
  try {
    final List<NetworkInterface> interfaces = await NetworkInterface.list(
      includeLoopback: false,
      type: InternetAddressType.IPv4,
    );
    for (final NetworkInterface interface in interfaces) {
      for (final InternetAddress address in interface.addresses) {
        final String ip = address.address;
        if (ip.startsWith('169.254.')) continue; // 链路本地地址没有意义
        final String base = subnetBase(ip);
        if (base.isNotEmpty) bases.add(base);
      }
    }
  } catch (_) {
    return const <String>[];
  }
  return bases.take(3).toList();
}

/// 自动发现局域网内的设备端服务。
///
/// 顺序：先问 mDNS 名字（同网段几乎瞬间命中），再并行扫本机各 `/24` 网段
/// 的 [port]，最后用 `GET /api/v1/system` 逐个确认。多个候选时优先
/// `instance_id` 以 `jetson` 开头的那个，避免认成同网段里别人的服务。
Future<DeviceProbe?> discoverDevice({
  int port = 8090,
  List<String>? hosts,
  List<String>? subnets,
  PortProbe? probe,
  SystemFetcher? fetch,
  List<String> mdnsNames = const <String>[
    'qwq.local',
    'jetson.local',
    'orin.local',
  ],
  Duration httpTimeout = const Duration(milliseconds: 1500),
}) async {
  final PortProbe portProbe = probe ?? probePort;
  final SystemFetcher fetcher =
      fetch ?? (Uri uri) => fetchSystem(uri, httpTimeout);

  Future<DeviceProbe?> confirm(String host) async {
    final DeviceProbe? found = parseSystemPayload(
      'http://$host:$port',
      await fetcher(Uri.parse('http://$host:$port/api/v1/system')),
    );
    return found;
  }

  // ========== Step1: mDNS 名字 ==========
  if (hosts == null) {
    for (final String name in mdnsNames) {
      List<InternetAddress> resolved;
      try {
        resolved = await InternetAddress.lookup(
          name,
        ).timeout(const Duration(milliseconds: 500));
      } catch (_) {
        continue;
      }
      for (final InternetAddress address in resolved) {
        if (address.type != InternetAddressType.IPv4) continue;
        if (!await portProbe(address.address, port)) continue;
        final DeviceProbe? found = await confirm(address.address);
        if (found != null) return found;
      }
    }
  }

  // ========== Step2: 扫 /24 ==========
  final List<String> targets = <String>[];
  if (hosts != null) {
    targets.addAll(hosts);
  } else {
    final List<String> bases = subnets ?? await localSubnets();
    for (final String base in bases) {
      for (int i = 1; i <= 254; i++) {
        targets.add('$base.$i');
      }
    }
  }
  if (targets.isEmpty) return null;

  // 每批 64 个并发：一个 /24 大约 1–2 秒，三个网段也不到 6 秒。
  const int batch = 64;
  final List<String> open = <String>[];
  for (int start = 0; start < targets.length; start += batch) {
    final List<String> slice = targets.sublist(
      start,
      (start + batch).clamp(0, targets.length),
    );
    final List<bool> results = await Future.wait(
      slice.map((String host) => portProbe(host, port)),
    );
    for (int i = 0; i < slice.length; i++) {
      if (results[i]) open.add(slice[i]);
    }
  }

  DeviceProbe? fallback;
  for (final String host in open) {
    final DeviceProbe? found = await confirm(host);
    if (found == null) continue;
    if (found.instanceId.toLowerCase().startsWith('jetson')) return found;
    fallback ??= found;
  }
  return fallback;
}
