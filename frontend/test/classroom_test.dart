// frontend/test/classroom_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/features/classroom/classroom_controller.dart';
import 'package:teaching_robot_frontend/features/classroom/classroom_page.dart';
import 'package:teaching_robot_frontend/features/classroom/mock_classroom_service.dart';
import 'package:teaching_robot_frontend/features/classroom/session_model.dart';

/// 推进完整模拟课堂到等待学生回答。
Future<void> advance(WidgetTester tester, [int ticks = 8]) async {
  for (int index = 0; index < ticks; index++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

void main() {
  testWidgets('页面录音按钮完成输入，文字请求失败保留草稿', (WidgetTester tester) async {
    tester.view.physicalSize = const Size(1366, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await tester.pumpWidget(
      MaterialApp(home: ClassroomPage(controller: controller)),
    );
    await tester.pump();
    await tester.tap(find.byKey(const Key('start-task')));
    await advance(tester);
    await tester.ensureVisible(find.byKey(const Key('record-button')));
    await tester.tap(find.byKey(const Key('record-button')));
    await tester.pump();
    expect(controller.state.recording, 'recording');
    await tester.tap(find.byKey(const Key('record-button')));
    await advance(tester);
    expect(
      service.commands,
      containsAll(['start_recording', 'finish_recording']),
    );
    expect(
      controller.state.messages
          .where((ClassMessage item) => item.role == 'student')
          .length,
      1,
    );
    await tester.enterText(find.byKey(const Key('message-input')), '杯子是什么颜色？');
    controller.selectScenario(DemoScenario.requestFailure);
    await tester.pump();
    await tester.tap(find.byKey(const Key('send-message')));
    await tester.pump();
    expect(controller.error, contains('请求失败'));
    expect(find.text('杯子是什么颜色？'), findsOneWidget);
    controller.selectScenario(DemoScenario.normal);
    await tester.pump();
    await tester.tap(find.byKey(const Key('send-message')));
    await advance(tester);
    expect(service.commands, contains('submit_text'));
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    service.dispose();
  });

  testWidgets('销毁自有页面时释放活动定时器和订阅', (WidgetTester tester) async {
    await tester.pumpWidget(const MaterialApp(home: ClassroomPage()));
    await tester.pump();
    await tester.tap(find.byKey(const Key('start-task')));
    await tester.pump();
    await tester.pumpWidget(const SizedBox());
    await tester.pump();
    expect(tester.takeException(), isNull);
  });

  testWidgets('正常课堂显示观察、回答及独立播放终态', (WidgetTester tester) async {
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await controller.initialize();
    expect(await controller.createTask('认识物体'), isTrue);
    await advance(tester, 2);
    expect(controller.state.observed, isTrue);
    expect(controller.state.messages.length, 1);
    expect(controller.state.playback, 'playing');
    await advance(tester);
    expect(controller.state.playback, 'completed');
    expect(controller.state.stage, 'awaiting_answer');
    expect(controller.state.actions.last.status, 'succeeded');
  });

  testWidgets('手动与自动结束录音只提交一条输入', (WidgetTester tester) async {
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
      recordTick: const Duration(milliseconds: 100),
      maxRecordingSeconds: 2,
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await controller.createTask('认识物体');
    await advance(tester);
    await service.startRecording();
    await advance(tester, 2);
    await service.finishRecording();
    await service.finishRecording();
    await advance(tester);
    expect(
      controller.state.messages
          .where((ClassMessage item) => item.role == 'student')
          .length,
      1,
    );
    expect(controller.state.recording, 'completed');
  });

  for (final DemoScenario scenario in [
    DemoScenario.silence,
    DemoScenario.asrFailure,
  ]) {
    testWidgets('$scenario 不生成学生输入且保留明确提示', (WidgetTester tester) async {
      final MockClassroomService service = MockClassroomService(
        step: const Duration(milliseconds: 100),
      );
      final ClassroomController controller = ClassroomController(service);
      addTearDown(controller.dispose);
      await controller.createTask('认识物体');
      await advance(tester);
      controller.selectScenario(scenario);
      await controller.record();
      await tester.pump();
      await controller.record();
      await advance(tester);
      expect(
        controller.state.messages.where(
          (ClassMessage item) => item.role == 'student',
        ),
        isEmpty,
      );
      expect(controller.state.note, isNotEmpty);
      expect(
        controller.state.recording,
        scenario == DemoScenario.silence ? 'completed' : 'failed',
      );
    });
  }

  testWidgets('合成失败保留回答文本和失败状态', (WidgetTester tester) async {
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    controller.selectScenario(DemoScenario.synthesisFailure);
    await controller.createTask('认识物体');
    await advance(tester);
    expect(controller.state.messages, isNotEmpty);
    expect(controller.state.playback, 'failed');
    expect(controller.state.actions.last.status, 'failed');
    expect(controller.state.note, contains('语音合成失败'));
  });

  testWidgets('任务取消先等待确认，迟到事件不能复活任务', (WidgetTester tester) async {
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await controller.createTask('认识物体');
    await advance(tester, 2);
    await controller.stop();
    await tester.pump();
    expect(controller.state.taskStatus, 'cancelling');
    await advance(tester);
    expect(controller.state.taskStatus, 'cancelled');
    expect(controller.state.playback, 'stopped');
    expect(controller.state.actions.last.status, 'cancelled');
    expect(
      service.commands.where((String command) => command == 'stop_task').length,
      1,
    );
    expect(controller.state.messages.length, 1);
  });

  testWidgets('识别期间停止不再生成输入', (WidgetTester tester) async {
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await controller.createTask('认识物体');
    await advance(tester);
    await service.startRecording();
    await service.finishRecording();
    await controller.stop();
    await advance(tester);
    expect(controller.state.recording, 'cancelled');
    expect(
      controller.state.messages.where(
        (ClassMessage item) => item.role == 'student',
      ),
      isEmpty,
    );
  });

  testWidgets('断线时停止不误报成功，重连恢复最新快照', (WidgetTester tester) async {
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await controller.createTask('认识物体');
    await tester.pump();
    controller.disconnect();
    await tester.pump();
    await controller.stop();
    expect(controller.connected, isFalse);
    expect(controller.error, contains('尚未送达'));
    expect(controller.state.taskStatus, 'running');
    expect(service.commands, isNot(contains('stop_task')));
    await advance(tester);
    expect(controller.state.messages, isEmpty);
    await controller.reconnect();
    expect(controller.state.messages.length, 1);
    expect(controller.state.playback, 'completed');
    expect(controller.connected, isTrue);
  });

  testWidgets('重复和乱序事件不覆盖较新的快照', (WidgetTester tester) async {
    final ClassroomController controller = ClassroomController(
      MockClassroomService(),
    );
    addTearDown(controller.dispose);
    controller.applyEvent(
      const SessionModel(sequence: 3, taskId: 1, taskStatus: 'cancelled'),
    );
    controller.applyEvent(
      const SessionModel(sequence: 2, taskId: 1, taskStatus: 'running'),
    );
    controller.applyEvent(
      const SessionModel(sequence: 3, taskId: 1, taskStatus: 'running'),
    );
    expect(controller.state.taskStatus, 'cancelled');
    controller.applyEvent(
      const SessionModel(instanceId: 'new-instance', sequence: 1),
    );
    expect(controller.connected, isFalse);
    expect(controller.error, contains('实例已变化'));
  });

  testWidgets('请求失败可以重试且不会伪造任务', (WidgetTester tester) async {
    final MockClassroomService service = MockClassroomService();
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    controller.selectScenario(DemoScenario.requestFailure);
    expect(await controller.createTask('认识物体'), isFalse);
    expect(controller.error, contains('请求失败'));
    expect(controller.state.taskId, 0);
    controller.selectScenario(DemoScenario.normal);
    expect(await controller.createTask('认识物体'), isTrue);
    await tester.pump();
    expect(controller.state.taskStatus, 'running');
    service.dispose();
  });

  testWidgets('播放期间拒绝录音', (WidgetTester tester) async {
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await controller.createTask('认识物体');
    await advance(tester, 2);
    expect(await controller.record(), isFalse);
    expect(controller.error, contains('音频设备'));
    expect(service.commands, isNot(contains('start_recording')));
    service.dispose();
  });

  for (final Size size in [const Size(1366, 900), const Size(390, 844)]) {
    testWidgets('页面 $size 无布局溢出且停止按钮调用正确入口', (WidgetTester tester) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final MockClassroomService service = MockClassroomService(
        step: const Duration(milliseconds: 100),
      );
      final ClassroomController controller = ClassroomController(service);
      addTearDown(controller.dispose);
      await tester.pumpWidget(
        MaterialApp(home: ClassroomPage(controller: controller)),
      );
      await tester.pump();
      await tester.tap(find.byKey(const Key('start-task')));
      await advance(tester);
      expect(tester.takeException(), isNull);
      await tester.ensureVisible(find.byKey(const Key('stop-task')));
      await tester.tap(find.byKey(const Key('stop-task')));
      await advance(tester);
      expect(service.commands, contains('stop_task'));
      expect(controller.state.taskStatus, 'cancelled');
      expect(tester.takeException(), isNull);
      controller.applyEvent(
        SessionModel(
          sequence: controller.state.sequence + 1,
          taskStatus: 'cancelled',
          observation: '这是用于验证移动布局的长观察结果。' * 25,
          messages: [ClassMessage(999, 'assistant', '长回答需要完整换行。' * 50)],
        ),
      );
      await tester.pump();
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    });
  }
}
