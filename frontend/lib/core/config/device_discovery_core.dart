// frontend/lib/core/config/device_discovery_core.dart

/// 设备端探测结果。
class DeviceProbe {
  const DeviceProbe({
    required this.baseUrl,
    required this.instanceId,
    required this.cameraAvailable,
  });

  /// 规范化后的服务地址，例如 `http://192.168.0.127:8090`。
  final String baseUrl;

  /// 设备端实例编号，来自 `GET /api/v1/system`。
  final String instanceId;

  /// 摄像头当前是否出图。
  final bool cameraAvailable;
}

/// 取 `/api/v1/system` 的 JSON；失败返回 null，不抛异常。
typedef SystemFetcher = Future<Map<String, Object?>?> Function(Uri uri);

/// 端口探测；只判断 TCP 能否连通。
typedef PortProbe = Future<bool> Function(String host, int port);

/// 从响应体解析设备端身份；不是设备端服务时返回 null。
///
/// 判定只用合同里的稳定字段：`code` 为 0、`data.instance_id` 存在、
/// `data.camera` 是对象。缺少任一字段说明对端是别的服务，不能当设备用。
DeviceProbe? parseSystemPayload(String baseUrl, Map<String, Object?>? payload) {
  if (payload == null) return null;
  if (payload['code'] != 0) return null;
  final Object? data = payload['data'];
  if (data is! Map) return null;
  final Object? instance = data['instance_id'];
  final Object? camera = data['camera'];
  if (instance is! String || instance.isEmpty) return null;
  if (camera is! Map) return null;
  final Object? available = camera['available'];
  return DeviceProbe(
    baseUrl: baseUrl.replaceAll(RegExp(r'/+$'), ''),
    instanceId: instance,
    cameraAvailable: available == true,
  );
}

/// `/24` 网段前缀，例如 `192.168.0.127` → `192.168.0`。
///
/// 只按前 24 位切分：现场网络不会是 /16 的大二层，按 /24 扫 254 个地址
/// 是延迟与覆盖率最平衡的选择。
String subnetBase(String ip) {
  final List<String> parts = ip.split('.');
  if (parts.length != 4) return '';
  return '${parts[0]}.${parts[1]}.${parts[2]}';
}
