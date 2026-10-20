// frontend/test/field_error_box_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/features/classroom/widgets/field_error_box.dart';

void main() {
  Widget harness(String? message) => MaterialApp(
    home: Scaffold(body: FieldErrorBox(message: message)),
  );

  /// 动画高度在 SizeTransition 上；内层 Container 保持固有尺寸。
  Finder slot() => find.byType(SizeTransition);

  testWidgets('无错误时不占高度，出现后展开显示文案', (WidgetTester tester) async {
    await tester.pumpWidget(harness(null));
    expect(find.byKey(const Key('field-error-box')), findsNothing);

    await tester.pumpWidget(harness('请输入 1–4000 字的内容。'));
    await tester.pump(const Duration(milliseconds: 120));
    // 弹出过程中文案已随内容淡入。
    expect(find.text('请输入 1–4000 字的内容。'), findsOneWidget);

    await tester.pumpAndSettle();
    expect(find.byKey(const Key('field-error-box')), findsOneWidget);
    expect(tester.getSize(slot()).height, greaterThan(0));
  });

  testWidgets('空串视为无错误，不显示小框', (WidgetTester tester) async {
    await tester.pumpWidget(harness(''));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('field-error-box')), findsNothing);
  });

  testWidgets('弹出与缩回均为平滑高度动画，不是瞬间跳变', (WidgetTester tester) async {
    await tester.pumpWidget(harness(null));
    await tester.pumpWidget(harness('请先开始一节课堂。'));

    // 高度动画中途：尺寸已部分展开但未到位。
    await tester.pump(const Duration(milliseconds: 80));
    final Size mid = tester.getSize(slot());
    await tester.pumpAndSettle();
    final Size full = tester.getSize(slot());
    expect(mid.height, greaterThan(0));
    expect(mid.height, lessThan(full.height));

    // 缩回：高度持续收缩，而不是直接消失。
    await tester.pumpWidget(harness(null));
    await tester.pump(const Duration(milliseconds: 80));
    final Size shrinking = tester.getSize(slot());
    expect(shrinking.height, lessThan(full.height));
    expect(shrinking.height, greaterThan(0));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('field-error-box')), findsNothing);
    expect(slot(), findsNothing);
  });

  testWidgets('错误文案切换时不重建为另一条静态文本', (WidgetTester tester) async {
    await tester.pumpWidget(harness('第一段错误'));
    await tester.pumpAndSettle();
    await tester.pumpWidget(harness('第二段错误'));
    await tester.pumpAndSettle();
    expect(find.text('第二段错误'), findsOneWidget);
    expect(find.text('第一段错误'), findsNothing);
  });
}
