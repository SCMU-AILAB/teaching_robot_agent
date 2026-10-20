// frontend/test/send_button_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_symbols_icons/symbols.dart';
import 'package:teaching_robot_frontend/features/classroom/widgets/send_button.dart';

void main() {
  Widget harness({required bool loading, VoidCallback? onPressed}) => MaterialApp(
    home: Scaffold(
      body: Center(
        child: SendButton(loading: loading, onPressed: onPressed),
      ),
    ),
  );

  testWidgets('发送时箭头向上滑出按钮，结束时从下方滑入', (WidgetTester tester) async {
    await tester.pumpWidget(harness(loading: false, onPressed: () {}));
    final Offset idle = tester.getCenter(
      find.byIcon(Symbols.arrow_upward_rounded),
    );

    await tester.pumpWidget(harness(loading: true));
    await tester.pump(const Duration(milliseconds: 150));

    final Finder arrow = find.byIcon(Symbols.arrow_upward_rounded);
    expect(arrow, findsOneWidget);
    final Offset exiting = tester.getCenter(arrow);
    // 出场只允许向上：纵坐标变小，横坐标保持不变。
    expect(exiting.dy, lessThan(idle.dy));
    expect(exiting.dx, idle.dx);

    // 等出场完成（raw=1，箭头被裁掉），再触发回程。
    await tester.pump(const Duration(milliseconds: 350));
    expect(find.byIcon(Symbols.arrow_upward_rounded), findsNothing);

    await tester.pumpWidget(harness(loading: false, onPressed: () {}));
    // 回程 420ms，箭头在 raw≤0.45（约 231ms 后）从按钮下方出现。
    await tester.pump(const Duration(milliseconds: 280));
    final Offset entering = tester.getCenter(
      find.byIcon(Symbols.arrow_upward_rounded),
    );
    expect(entering.dy, greaterThan(idle.dy));
    expect(entering.dx, idle.dx);

    await tester.pump(const Duration(milliseconds: 200));
    final Offset settled = tester.getCenter(
      find.byIcon(Symbols.arrow_upward_rounded),
    );
    expect(settled.dy, idle.dy);
    expect(settled.dx, idle.dx);
  });

  testWidgets('处理中切换为旋转环，处理完成后回到箭头', (WidgetTester tester) async {
    await tester.pumpWidget(harness(loading: false, onPressed: () {}));
    expect(find.byIcon(Symbols.arrow_upward_rounded), findsOneWidget);
    expect(find.byKey(const ValueKey('spinner')), findsNothing);

    await tester.pumpWidget(harness(loading: true));
    await tester.pump(const Duration(milliseconds: 500));
    expect(find.byKey(const ValueKey('spinner')), findsOneWidget);
    expect(find.byIcon(Symbols.arrow_upward_rounded), findsNothing);

    await tester.pumpWidget(harness(loading: false, onPressed: () {}));
    await tester.pump(const Duration(milliseconds: 500));
    expect(find.byIcon(Symbols.arrow_upward_rounded), findsOneWidget);
    expect(find.byKey(const ValueKey('spinner')), findsNothing);
  });

  testWidgets('处理中不可点击，空闲时可点击', (WidgetTester tester) async {
    int taps = 0;
    await tester.pumpWidget(harness(loading: false, onPressed: () => taps++));
    await tester.tap(find.byKey(const Key('send-message')));
    expect(taps, 1);

    await tester.pumpWidget(harness(loading: true, onPressed: () => taps++));
    await tester.pump(const Duration(milliseconds: 500));
    await tester.tap(find.byKey(const Key('send-message')));
    expect(taps, 1);
  });

  testWidgets('仅禁用但未处理时显示静态箭头', (WidgetTester tester) async {
    await tester.pumpWidget(harness(loading: false));
    expect(find.byIcon(Symbols.arrow_upward_rounded), findsOneWidget);
    expect(find.byKey(const ValueKey('spinner')), findsNothing);
  });
}
