// frontend/lib/core/config/device_endpoint.dart

/// 设备服务地址解析，地址不写进业务代码。
///
/// 优先级（高 → 低）：
/// 1. 运行时保存的地址（设置里填的公网 IP，见 [EndpointStore]）；
/// 2. 页面查询参数 `?api=http://host:port`（Web 调试用）；
/// 3. 构建期注入 `--dart-define=DEVICE_API_BASE=...`；
/// 4. 页面同源（页面由设备端托管时命中）；
/// 5. 本机 `127.0.0.1:8090`。
///
/// [base] 仅用于测试注入页面地址，默认取 `Uri.base`。
String resolveDeviceBaseUrl({Uri? base, String? saved}) {
  final String? stored = saved?.trim();
  if (stored != null && _isHttpUrl(stored)) return _trim(stored);

  final Uri page = base ?? Uri.base;
  final String override = page.queryParameters['api'] ?? '';
  if (_isHttpUrl(override)) return _trim(override);

  const String configured = String.fromEnvironment('DEVICE_API_BASE');
  if (configured.isNotEmpty) return configured;

  if (page.hasScheme && (page.scheme == 'http' || page.scheme == 'https')) {
    return page.origin;
  }
  return 'http://127.0.0.1:8090';
}

/// 地址是否合法：必须带 http/https 协议与主机名，不接受其它协议。
bool _isHttpUrl(String value) {
  final Uri? uri = Uri.tryParse(value);
  if (uri == null || !uri.hasAuthority) return false;
  return uri.scheme == 'http' || uri.scheme == 'https';
}

/// 去掉末尾斜杠，避免拼出 `//api/v1`。
String _trim(String value) => value.replaceAll(RegExp(r'/+$'), '');

/// 供设置对话框校验用户输入：合法返回 null，否则返回提示文案。
String? validateDeviceBaseUrl(String value) {
  final String trimmed = value.trim();
  if (trimmed.isEmpty) return '请输入设备服务地址';
  final Uri? uri = Uri.tryParse(trimmed);
  if (uri == null || !_isHttpUrl(trimmed)) {
    return '地址需以 http:// 或 https:// 开头，例如 http://1.2.3.4:8090';
  }
  if (uri.host.isEmpty) return '缺少主机名';
  if (uri.hasQuery || uri.hasFragment) return '地址不要带参数或片段';
  return null;
}
