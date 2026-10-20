// frontend/test/device_endpoint_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/core/config/device_endpoint.dart';

void main() {
  test('未注入 DEVICE_API_BASE 且无 http(s) 同源地址时回退本机端口', () {
    // 测试进程的 Uri.base 是 file://，因此走最后一级回退。
    expect(resolveDeviceBaseUrl(), 'http://127.0.0.1:8090');
  });

  test('页面同源优先于本机回退', () {
    expect(
      resolveDeviceBaseUrl(base: Uri.parse('http://192.168.0.127:8090/')),
      'http://192.168.0.127:8090',
    );
  });

  test('查询参数 api 可覆盖设备地址，末尾斜杠被去掉', () {
    expect(
      resolveDeviceBaseUrl(
        base: Uri.parse('http://192.168.0.127:8000/?api=http://10.0.0.5:8090//'),
      ),
      'http://10.0.0.5:8090',
    );
  });

  test('非法 api 参数被忽略，仍按同源处理', () {
    expect(
      resolveDeviceBaseUrl(
        base: Uri.parse('http://192.168.0.127:8000/?api=javascript:alert(1)'),
      ),
      'http://192.168.0.127:8000',
    );
  });
}
