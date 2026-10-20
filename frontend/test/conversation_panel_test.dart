// frontend/test/conversation_panel_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/features/classroom/session_model.dart';
import 'package:teaching_robot_frontend/features/classroom/widgets/conversation_panel.dart';

Widget harness(SessionModel state, GlobalKey inputKey) => MaterialApp(
  home: Scaffold(
    body: Column(
      children: [
        Expanded(child: ConversationPanel(state: state, messageInputKey: inputKey)),
        KeyedSubtree(key: inputKey, child: const SizedBox(height: 40)),
      ],
    ),
  ),
);

SessionModel withMessages(List<ClassMessage> messages) =>
    SessionModel(messages: messages);

void main() {
  testWidgets('滚动回收重建后不重播发送飞入', (WidgetTester tester) async {
    final GlobalKey inputKey = GlobalKey();
    await tester.pumpWidget(harness(const SessionModel(), inputKey));
    await tester.pump();
    // 先只来一条学生消息：播一次飞入，把入场记入 _animated。
    await tester.pumpWidget(
      harness(withMessages(const [ClassMessage(1, 'student', '第一句话')]), inputKey),
    );
    await tester.pump(const Duration(milliseconds: 150));
    await tester.pumpAndSettle();
    expect(find.text('第一句话'), findsOneWidget);

    // 追加大量消息把「第一句话」顶出 345px 视口（触发 ListView 回收）。
    final List<ClassMessage> many = [
      const ClassMessage(1, 'student', '第一句话'),
      for (int i = 2; i <= 24; i++)
        ClassMessage(i, 'assistant', '填充回答 $i'),
    ];
    await tester.pumpWidget(harness(withMessages(many), inputKey));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pumpAndSettle();

    // 上下滚动：让首条经历「移出视口 → 回收 → 重建」。
    // 列表自动停在底部，需滚回顶部让首条重新建出。
    await tester.drag(find.byType(ListView), const Offset(0, -800));
    await tester.pumpAndSettle();
    await tester.drag(find.byType(ListView), const Offset(0, 4000));
    await tester.pump(const Duration(milliseconds: 120));
    await tester.pumpAndSettle();

    // 重建后不得再出现飞入前的透明占位或新幽灵（重播证据）。
    final Iterable<Opacity> ghosts = tester
        .widgetList<Opacity>(find.byType(Opacity))
        .where((Opacity o) => o.opacity == 0);
    expect(ghosts, isEmpty);
    expect(find.text('第一句话'), findsOneWidget);
    await tester.pumpAndSettle();
  });

  testWidgets('回答等待学生飞入完成后才显示', (WidgetTester tester) async {
    final GlobalKey inputKey = GlobalKey();
    await tester.pumpWidget(harness(const SessionModel(), inputKey));
    await tester.pump();

    // 学生消息与回答同帧到达（模拟服务端快速应答）。
    await tester.pumpWidget(
      harness(
        withMessages(const [
          ClassMessage(1, 'student', '杯子是什么颜色？'),
          ClassMessage(2, 'assistant', '杯子是绿色的。'),
        ]),
        inputKey,
      ),
    );
    await tester.pump();
    // 飞入进行中：气泡文字已在树上（幽灵 + 透明占位），回答仍在排队。
    expect(find.text('杯子是什么颜色？'), findsWidgets);
    expect(find.text('杯子是绿色的。'), findsNothing);

    // 形变 680ms + 弹两下 560ms 完成后回答放行。
    await tester.pump(const Duration(milliseconds: 1400));
    await tester.pump();
    expect(find.text('杯子是绿色的。'), findsOneWidget);
    await tester.pumpAndSettle();
  });

  testWidgets('旧消息直接显示不重播飞入', (WidgetTester tester) async {
    final GlobalKey inputKey = GlobalKey();
    await tester.pumpWidget(
      harness(
        withMessages(const [
          ClassMessage(1, 'student', '历史问题'),
          ClassMessage(2, 'assistant', '历史回答'),
        ]),
        inputKey,
      ),
    );
    await tester.pump();
    expect(find.text('历史问题'), findsOneWidget);
    expect(find.text('历史回答'), findsOneWidget);
    await tester.pumpAndSettle();
  });
}
