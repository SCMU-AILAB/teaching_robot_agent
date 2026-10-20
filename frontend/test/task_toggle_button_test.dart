// frontend/test/task_toggle_button_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/features/classroom/widgets/rolling_text.dart';
import 'package:teaching_robot_frontend/features/classroom/widgets/task_toggle_button.dart';

void main() {
  Widget harness({
    required bool active,
    bool cancelling = false,
    VoidCallback? onStart,
    VoidCallback? onStop,
  }) => MaterialApp(
    home: Scaffold(
      body: Center(
        child: TaskToggleButton(
          active: active,
          cancelling: cancelling,
          onStart: onStart,
          onStop: onStop,
        ),
      ),
    ),
  );

  testWidgets('按状态切换动词并调用对应回调', (WidgetTester tester) async {
    int starts = 0;
    int stops = 0;

    await tester.pumpWidget(
      harness(
        active: false,
        onStart: () => starts++,
        onStop: () => stops++,
      ),
    );
    // 动词逐字渲染，「课堂」保持完整且静止。
    expect(find.text('开'), findsOneWidget);
    expect(find.text('始'), findsOneWidget);
    expect(find.text('课堂'), findsOneWidget);
    await tester.tap(find.byKey(const Key('task-toggle')));
    expect(starts, 1);
    expect(stops, 0);

    await tester.pumpWidget(
      harness(active: true, onStart: () => starts++, onStop: () => stops++),
    );
    await tester.pump(const Duration(milliseconds: 700));
    expect(find.text('停'), findsOneWidget);
    expect(find.text('止'), findsOneWidget);
    expect(find.text('课堂'), findsOneWidget);
    await tester.tap(find.byKey(const Key('task-toggle')));
    expect(stops, 1);
  });

  testWidgets('取消确认期间文案不变且不可点击', (WidgetTester tester) async {
    int stops = 0;
    await tester.pumpWidget(harness(active: true, onStop: () => stops++));
    await tester.pumpWidget(
      harness(active: true, cancelling: true, onStop: () => stops++),
    );
    await tester.pump(const Duration(milliseconds: 700));
    expect(find.text('停'), findsOneWidget);
    expect(find.text('止'), findsOneWidget);
    expect(find.text('正在停止'), findsNothing);
    await tester.tap(find.byKey(const Key('task-toggle')));
    expect(stops, 0);
  });

  testWidgets('切换状态时按钮内部发生逐字滚动', (WidgetTester tester) async {
    await tester.pumpWidget(harness(active: false));
    expect(find.text('开'), findsOneWidget);
    expect(find.text('停'), findsNothing);

    await tester.pumpWidget(harness(active: true));
    await tester.pump(const Duration(milliseconds: 160));

    // 转轮进行中：旧字仍在淡出、新字已进入，两者共存。
    // 若按钮子树被重建，这里只会剩下新字，断言会失败。
    expect(find.text('开'), findsOneWidget);
    expect(find.text('停'), findsOneWidget);

    await tester.pump(const Duration(milliseconds: 800));
    expect(find.text('开'), findsNothing);
    expect(find.text('停'), findsOneWidget);
  });

  testWidgets('滚动期间按钮宽度不变，字符只做垂直位移', (WidgetTester tester) async {
    await tester.pumpWidget(harness(active: false));
    final double idleWidth = tester.getSize(find.byType(TaskToggleButton)).width;
    final double idleDx = tester.getTopLeft(find.text('开')).dx;

    await tester.pumpWidget(harness(active: true));
    await tester.pump(const Duration(milliseconds: 80));

    // 窗口宽度稳定：按钮不得因字符切换而变宽/变窄。
    expect(tester.getSize(find.byType(TaskToggleButton)).width, idleWidth);
    // 新字与旧字横向锚点一致：只允许上下移动，不允许左右漂移。
    expect(tester.getTopLeft(find.text('停')).dx, idleDx);

    await tester.pump(const Duration(milliseconds: 500));
    expect(tester.getSize(find.byType(TaskToggleButton)).width, idleWidth);
  });

  testWidgets('开始不可用时不触发回调', (WidgetTester tester) async {
    await tester.pumpWidget(harness(active: false));
    await tester.tap(find.byKey(const Key('task-toggle')));
    expect(tester.takeException(), isNull);
  });

  testWidgets('逐字滚动时新旧字符同时存在', (WidgetTester tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: RollingText('开始'))),
    );
    expect(find.text('开'), findsOneWidget);

    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: RollingText('停止'))),
    );
    await tester.pump(const Duration(milliseconds: 120));
    // 转轮进行中：旧字与新字在同一位置叠加，说明是逐字滚动而非整块替换。
    expect(find.text('开'), findsOneWidget);
    expect(find.text('停'), findsOneWidget);

    await tester.pump(const Duration(milliseconds: 700));
    expect(find.text('停'), findsOneWidget);
    expect(find.text('止'), findsOneWidget);
    expect(find.text('开'), findsNothing);
    expect(find.text('始'), findsNothing);
  });
}
