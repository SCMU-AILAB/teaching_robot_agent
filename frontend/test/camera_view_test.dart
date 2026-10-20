// frontend/test/camera_view_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/features/classroom/widgets/camera_view.dart';

void main() {
  testWidgets('数据源没有摄像头时显示未连接占位，不发请求', (WidgetTester tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: CameraView(streamUrl: null))),
    );
    expect(find.text('未连接服务器'), findsOneWidget);
    expect(find.text('检查设备服务地址与网络后重试'), findsOneWidget);
  });

  testWidgets('有流地址但无快照地址时仍给出占位而不是空白画面', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: CameraView(
            streamUrl: Uri.parse('http://device.test/api/v1/camera/stream'),
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.text('未连接服务器'), findsOneWidget);
  });
}
