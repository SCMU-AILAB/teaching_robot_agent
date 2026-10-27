// frontend/test/record_toggle_button_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/features/classroom/widgets/record_toggle_button.dart';

void main() {
  Widget harness({required bool recording, VoidCallback? onPressed}) =>
      MaterialApp(
        home: Scaffold(
          body: Center(
            child: RecordToggleButton(
              recording: recording,
              onPressed: onPressed ?? () {},
            ),
          ),
        ),
      );

  testWidgets('切换时动词逐字滚动，录音二字保持静止', (WidgetTester tester) async {
    await tester.pumpWidget(harness(recording: false));
    expect(find.text('开'), findsOneWidget);
    expect(find.text('始'), findsOneWidget);
    expect(find.text('录音'), findsOneWidget);

    await tester.pumpWidget(harness(recording: true));
    await tester.pump(const Duration(milliseconds: 160));

    // 转轮进行中：旧字与新字共存；若子树被重建只会剩新字，断言会失败。
    expect(find.text('开'), findsOneWidget);
    expect(find.text('结'), findsOneWidget);
    expect(find.text('录音'), findsOneWidget);

    await tester.pump(const Duration(milliseconds: 800));
    expect(find.text('开'), findsNothing);
    expect(find.text('结'), findsOneWidget);
    expect(find.text('录音'), findsOneWidget);
  });

  testWidgets('切换状态时按钮宽度与字符横向锚点不变', (WidgetTester tester) async {
    await tester.pumpWidget(harness(recording: false));
    final double idleWidth = tester
        .getSize(find.byKey(const Key('record-button')))
        .width;
    final double idleDx = tester.getTopLeft(find.text('开')).dx;

    await tester.pumpWidget(harness(recording: true));
    await tester.pump(const Duration(milliseconds: 80));

    expect(
      tester.getSize(find.byKey(const Key('record-button'))).width,
      idleWidth,
    );
    // 新旧字横向锚点一致：只允许上下卷动，不允许左右漂移。
    expect(tester.getTopLeft(find.text('结')).dx, idleDx);

    await tester.pump(const Duration(milliseconds: 500));
    expect(
      tester.getSize(find.byKey(const Key('record-button'))).width,
      idleWidth,
    );
  });

  testWidgets('点击触发回调，禁用时不可点击', (WidgetTester tester) async {
    int taps = 0;
    await tester.pumpWidget(harness(recording: false, onPressed: () => taps++));
    await tester.tap(find.byKey(const Key('record-button')));
    expect(taps, 1);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: RecordToggleButton(recording: false, onPressed: null),
        ),
      ),
    );
    await tester.pump(const Duration(milliseconds: 400));
    await tester.tap(find.byKey(const Key('record-button')));
    expect(taps, 1);
    expect(tester.takeException(), isNull);
  });
}
