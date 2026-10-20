// frontend/test/device_endpoint_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/core/config/device_endpoint.dart';

void main() {
  test('未注入 DEVICE_API_BASE 且无 http(s) 同源地址时回退本机端口', () {
    // 测试进程的 Uri.base 是 file://，因此走最后一级回退。
    expect(resolveDeviceBaseUrl(), 'http://127.0.0.1:8090');
  });
}
