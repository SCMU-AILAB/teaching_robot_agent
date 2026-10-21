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

  test('运行时保存的公网地址优先级最高', () {
    expect(
      resolveDeviceBaseUrl(
        base: Uri.parse('http://192.168.0.127:8000/?api=http://10.0.0.5:8090'),
        saved: ' https://robot.example.com:8443 ',
      ),
      'https://robot.example.com:8443',
    );
  });

  test('保存的地址非法时忽略，继续按页面地址解析', () {
    expect(
      resolveDeviceBaseUrl(
        base: Uri.parse('http://192.168.0.127:8000/'),
        saved: 'robot.example.com:8443',
      ),
      'http://192.168.0.127:8000',
    );
  });

  test('设置框校验：协议、主机、参数', () {
    expect(validateDeviceBaseUrl(''), isNotNull);
    expect(validateDeviceBaseUrl('192.168.0.127:8090'), isNotNull);
    expect(validateDeviceBaseUrl('ftp://1.2.3.4:8090'), isNotNull);
    expect(validateDeviceBaseUrl('http://1.2.3.4:8090/?a=1'), isNotNull);
    expect(validateDeviceBaseUrl('http://1.2.3.4:8090'), isNull);
    expect(validateDeviceBaseUrl('https://robot.example.com'), isNull);
  });
}
