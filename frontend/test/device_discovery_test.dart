// frontend/test/device_discovery_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/core/config/device_discovery.dart';

/// 构造一份合同内的 /system 响应体。
Map<String, Object?> systemPayload({
  String instance = 'jetson-818',
  bool camera = true,
}) => <String, Object?>{
  'code': 0,
  'message': 'success',
  'data': <String, Object?>{
    'instance_id': instance,
    'camera': <String, Object?>{'available': camera},
  },
};

void main() {
  group('parseSystemPayload', () {
    test('合同结构解析出实例与摄像头状态', () {
      final DeviceProbe? probe = parseSystemPayload(
        'http://192.168.0.127:8090',
        systemPayload(camera: false),
      );
      expect(probe, isNotNull);
      expect(probe!.instanceId, 'jetson-818');
      expect(probe.cameraAvailable, isFalse);
      expect(probe.baseUrl, 'http://192.168.0.127:8090');
    });

    test('地址末尾斜杠被规范化', () {
      final DeviceProbe? probe = parseSystemPayload(
        'http://10.0.0.5:8090/',
        systemPayload(),
      );
      expect(probe!.baseUrl, 'http://10.0.0.5:8090');
    });

    test('缺少 camera 字段视为不是设备端', () {
      final Map<String, Object?> payload = systemPayload();
      (payload['data']! as Map<String, Object?>).remove('camera');
      expect(parseSystemPayload('http://10.0.0.5:8090', payload), isNull);
    });

    test('code 非 0、无响应体、data 非对象都拒绝', () {
      expect(
        parseSystemPayload('http://10.0.0.5:8090', <String, Object?>{
          'code': 50001,
        }),
        isNull,
      );
      expect(parseSystemPayload('http://10.0.0.5:8090', null), isNull);
      expect(
        parseSystemPayload('http://10.0.0.5:8090', <String, Object?>{
          'code': 0,
          'data': 'not-a-map',
        }),
        isNull,
      );
    });
  });

  group('subnetBase', () {
    test('取前 24 位', () {
      expect(subnetBase('192.168.0.127'), '192.168.0');
      expect(subnetBase('10.1.2.3'), '10.1.2');
    });

    test('非法输入返回空串', () {
      expect(subnetBase('192.168.0'), '');
      expect(subnetBase(''), '');
    });
  });

  group('probeDevice', () {
    test('注入取数函数时按合同判定', () async {
      final DeviceProbe? probe = await probeDevice(
        'http://192.168.0.127:8090',
        fetch: (Uri uri) async {
          expect(uri.path, '/api/v1/system');
          return systemPayload();
        },
      );
      expect(probe!.instanceId, 'jetson-818');
    });

    test('取不到响应返回 null，不抛异常', () async {
      final DeviceProbe? probe = await probeDevice(
        'http://192.168.0.127:8090',
        fetch: (Uri uri) async => null,
      );
      expect(probe, isNull);
    });

    test('地址不合法直接返回 null', () async {
      var called = false;
      final DeviceProbe? probe = await probeDevice(
        '192.168.0.127:8090',
        fetch: (Uri uri) async {
          called = true;
          return systemPayload();
        },
      );
      expect(probe, isNull);
      expect(called, isFalse);
    });
  });

  group('discoverDevice', () {
    test('只认开了端口且响应合同结构的地址', () async {
      final Set<String> probed = <String>{};
      final DeviceProbe? found = await discoverDevice(
        hosts: <String>['10.0.0.1', '10.0.0.2', '10.0.0.3'],
        probe: (String host, int port) async {
          probed.add(host);
          return host == '10.0.0.2';
        },
        fetch: (Uri uri) async => uri.host == '10.0.0.2'
            ? systemPayload()
            : null,
      );
      expect(probed, <String>{'10.0.0.1', '10.0.0.2', '10.0.0.3'});
      expect(found!.baseUrl, 'http://10.0.0.2:8090');
    });

    test('端口开着但响应不是设备端时跳过', () async {
      final DeviceProbe? found = await discoverDevice(
        hosts: <String>['10.0.0.7'],
        probe: (String host, int port) async => true,
        fetch: (Uri uri) async => <String, Object?>{'code': 0, 'data': 42},
      );
      expect(found, isNull);
    });

    test('多个候选优先 instance_id 以 jetson 开头', () async {
      final DeviceProbe? found = await discoverDevice(
        hosts: <String>['10.0.0.4', '10.0.0.5'],
        probe: (String host, int port) async => true,
        fetch: (Uri uri) async => uri.host == '10.0.0.4'
            ? systemPayload(instance: 'other-board')
            : systemPayload(instance: 'jetson-orin'),
      );
      expect(found!.instanceId, 'jetson-orin');
      expect(found.baseUrl, 'http://10.0.0.5:8090');
    });

    test('没有 jetson 时退回第一个可用设备', () async {
      final DeviceProbe? found = await discoverDevice(
        hosts: <String>['10.0.0.4', '10.0.0.5'],
        probe: (String host, int port) async => true,
        fetch: (Uri uri) async => systemPayload(instance: 'lab-pi'),
      );
      expect(found!.instanceId, 'lab-pi');
      expect(found.baseUrl, 'http://10.0.0.4:8090');
    });

    test('全都不通返回 null', () async {
      final DeviceProbe? found = await discoverDevice(
        hosts: <String>['10.0.0.8', '10.0.0.9'],
        probe: (String host, int port) async => false,
        fetch: (Uri uri) async => systemPayload(),
      );
      expect(found, isNull);
    });

    test('没有可扫网段时不发起探测', () async {
      var called = false;
      final DeviceProbe? found = await discoverDevice(
        subnets: const <String>[],
        mdnsNames: const <String>[],
        probe: (String host, int port) async {
          called = true;
          return true;
        },
        fetch: (Uri uri) async => systemPayload(),
      );
      expect(found, isNull);
      expect(called, isFalse);
    });
  });
}
