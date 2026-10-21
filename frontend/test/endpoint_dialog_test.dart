// frontend/test/endpoint_dialog_test.dart
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/features/classroom/widgets/endpoint_dialog.dart';

/// 打开对话框并返回弹出的结果。
Future<String?> openDialog(
  WidgetTester tester, {
  required String current,
  EndpointProbe? probe,
}) async {
  String? result;
  await tester.pumpWidget(
    MaterialApp(
      home: Builder(
        builder: (BuildContext context) => TextButton(
          onPressed: () async {
            result = await showDialog<String>(
              context: context,
              builder: (_) =>
                  EndpointDialog(current: current, probe: probe ?? (_) async => 'ok'),
            );
          },
          child: const Text('open'),
        ),
      ),
    ),
  );
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
  return result;
}

void main() {
  testWidgets('非法地址不发探测、不关闭对话框', (WidgetTester tester) async {
    await openDialog(tester, current: 'http://1.2.3.4:8090');
    await tester.enterText(find.byKey(const Key('endpoint-input')), '1.2.3.4:8090');
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('endpoint-error')), findsOneWidget);
    expect(find.text('设备服务地址'), findsOneWidget);
  });

  testWidgets('测试连接成功显示实例与摄像头状态', (WidgetTester tester) async {
    await openDialog(
      tester,
      current: 'http://1.2.3.4:8090',
      probe: (String base) async => '连接成功 · jetson-818 · 摄像头未就绪',
    );
    await tester.tap(find.text('测试连接'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('endpoint-probe')), findsOneWidget);
    expect(find.text('连接成功 · jetson-818 · 摄像头未就绪'), findsOneWidget);
  });

  testWidgets('测试连接失败给出可操作提示', (WidgetTester tester) async {
    await openDialog(
      tester,
      current: 'http://1.2.3.4:8090',
      probe: (String base) async => throw TimeoutException('slow'),
    );
    await tester.tap(find.text('测试连接'));
    await tester.pumpAndSettle();
    expect(find.textContaining('连接超时'), findsOneWidget);
  });

  testWidgets('保存返回去掉末尾斜杠的地址', (WidgetTester tester) async {
    String? popped;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (BuildContext context) => TextButton(
            onPressed: () async {
              popped = await showDialog<String>(
                context: context,
                builder: (_) => const EndpointDialog(current: 'http://1.2.3.4:8090'),
              );
            },
            child: const Text('open'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const Key('endpoint-input')),
      'https://robot.example.com:8443/',
    );
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();
    expect(popped, 'https://robot.example.com:8443');
  });
}
