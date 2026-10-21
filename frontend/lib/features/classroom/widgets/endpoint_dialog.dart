// frontend/lib/features/classroom/widgets/endpoint_dialog.dart
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

import '../../../core/config/device_discovery.dart';
import '../../../core/config/device_endpoint.dart';

/// 探测设备服务：成功返回摘要文案，失败抛异常。
typedef EndpointProbe = Future<String> Function(String baseUrl);

/// 默认探测：复用设备发现模块的校验，回显实例名与摄像头状态。
///
/// 与自动扫描用同一套判定（`code`/`data.instance_id`/`data.camera`），
/// 避免设置框说「连接成功」而扫描认不出同一台设备。
Future<String> describeDevice(String baseUrl) async {
  final DeviceProbe? probe = await probeDevice(baseUrl);
  if (probe == null) {
    throw const FormatException('未收到设备端响应，检查地址、端口与网络');
  }
  final String camera = probe.cameraAvailable ? '摄像头就绪' : '摄像头未就绪';
  return '连接成功 · ${probe.instanceId} · $camera';
}

/// 设备服务地址设置框：Mac 端通过公网 IP 连 Jetson 时在此填写。
///
/// 保存前可以「测试连接」验证地址可达；点保存返回新地址（取消返回 null）。
class EndpointDialog extends StatefulWidget {
  const EndpointDialog({required this.current, this.probe = describeDevice, super.key});

  /// 当前生效地址。
  final String current;

  /// 探测实现，测试可替换。
  final EndpointProbe probe;

  @override
  State<EndpointDialog> createState() => _EndpointDialogState();
}

class _EndpointDialogState extends State<EndpointDialog> {
  late final TextEditingController _input = TextEditingController(
    text: widget.current,
  );

  String? _error;
  String _probe = '';
  bool _probing = false;

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  String get _value => _input.text.trim();

  /// 用探测函数验一次；不建控制器、不动页面状态。
  Future<void> _test() async {
    final String? invalid = validateDeviceBaseUrl(_value);
    if (invalid != null) {
      setState(() {
        _error = invalid;
        _probe = '';
      });
      return;
    }
    setState(() {
      _probing = true;
      _error = null;
      _probe = '';
    });
    try {
      final String summary = await widget.probe(
        _value.replaceAll(RegExp(r'/+$'), ''),
      );
      if (mounted) setState(() => _probe = summary);
    } on TimeoutException {
      if (mounted) setState(() => _error = '连接超时（6 秒），检查公网 IP 与端口映射');
    } on FormatException catch (failure) {
      if (mounted) setState(() => _error = '地址通了但不是设备服务：${failure.message}');
    } catch (_) {
      if (mounted) setState(() => _error = '连接失败，检查地址、端口映射与设备是否在线');
    } finally {
      if (mounted) setState(() => _probing = false);
    }
  }

  void _save() {
    final String? invalid = validateDeviceBaseUrl(_value);
    if (invalid != null) {
      setState(() => _error = invalid);
      return;
    }
    Navigator.of(context).pop(_value.replaceAll(RegExp(r'/+$'), ''));
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
    title: const Row(
      children: [
        Icon(Symbols.dns_rounded, size: 20),
        SizedBox(width: 8),
        Text('设备服务地址', style: TextStyle(fontSize: 17)),
      ],
    ),
    content: SizedBox(
      width: 420,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            key: const Key('endpoint-input'),
            controller: _input,
            autofocus: true,
            decoration: const InputDecoration(
              labelText: '服务地址',
              hintText: 'http://<公网IP>:8090',
            ),
            onSubmitted: (_) => _save(),
          ),
          const SizedBox(height: 10),
          const Text(
            'Mac 端通过公网 IP 连 Jetson 时填它的对外地址；内网调试填 192.168.x.x:8090。',
            style: TextStyle(fontSize: 12, color: Color(0xff73817b), height: 1.5),
          ),
          if (_error != null) ...[
            const SizedBox(height: 10),
            Text(
              _error!,
              key: const Key('endpoint-error'),
              style: const TextStyle(fontSize: 12, color: Color(0xffb6403a)),
            ),
          ],
          if (_probe.isNotEmpty) ...[
            const SizedBox(height: 10),
            Row(
              children: [
                const Icon(
                  Symbols.check_circle_rounded,
                  size: 14,
                  color: Color(0xff147d64),
                ),
                const SizedBox(width: 6),
                Flexible(
                  child: Text(
                    _probe,
                    key: const Key('endpoint-probe'),
                    style: const TextStyle(
                      fontSize: 12,
                      color: Color(0xff147d64),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: _probing ? null : () => Navigator.of(context).pop(),
        child: const Text('取消'),
      ),
      TextButton(
        onPressed: _probing ? null : () => unawaited(_test()),
        child: Text(_probing ? '测试中…' : '测试连接'),
      ),
      FilledButton(onPressed: _probing ? null : _save, child: const Text('保存')),
    ],
  );
}
