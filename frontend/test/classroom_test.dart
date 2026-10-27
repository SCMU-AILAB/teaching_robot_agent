// frontend/test/classroom_test.dart
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/features/classroom/classroom_controller.dart';
import 'package:teaching_robot_frontend/features/classroom/classroom_page.dart';
import 'package:teaching_robot_frontend/features/classroom/mock_classroom_service.dart';
import 'package:teaching_robot_frontend/features/classroom/session_model.dart';

/// 推进完整模拟课堂到等待学生回答。
/// 默认 14 tick 覆盖「转写 + 回答延迟 4 step + 播报链」的全部计时器。
Future<void> advance(WidgetTester tester, [int ticks = 14]) async {
  for (int index = 0; index < ticks; index++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

/// 控制快照返回时机，复现事件先到而旧快照迟到的顺序。
class DelayedSnapshotService extends MockClassroomService {
  Completer<SessionModel>? pending;

  @override
  Future<SessionModel> snapshot() => pending?.future ?? super.snapshot();
}

/// 快照失败/成功可切换，模拟设备不可达后恢复。
class FlakySnapshotService extends MockClassroomService {
  bool fail = true;

  @override
  Future<SessionModel> snapshot() async {
    if (fail) throw const ClassroomFailure('设备不可达');
    return super.snapshot();
  }
}

void main() {
  testWidgets('首次观察期间拒绝新输入，避免重叠播报覆盖动作', (tester) async {
    final service = MockClassroomService(
      step: const Duration(milliseconds: 100),
    );
    final controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await controller.createTask('观察桌面');
    // 刷新观察也不能跳过初始化中的输入屏障。
    await controller.refresh();
    expect(await controller.submitText('提前提问'), isFalse);
    expect(await controller.record(), isFalse);
    await advance(tester);
    expect(controller.state.messages.length, 1);
    expect(
      controller.state.actions.every((action) => action.status == 'succeeded'),
      isTrue,
    );
    expect(await controller.submitText('现在提问'), isTrue);
    await advance(tester);
    expect(controller.state.messages.length, 3);
  });

  testWidgets('迟到快照不得覆盖更新事件', (tester) async {
    final service = DelayedSnapshotService();
    final controller = ClassroomController(service);
    addTearDown(controller.dispose);
    service.pending = Completer<SessionModel>();
    final request = controller.initialize();
    controller.applyEvent(
      const SessionModel(sequence: 5, taskStatus: 'cancelled'),
    );
    service.pending!.complete(
      const SessionModel(sequence: 2, taskStatus: 'running'),
    );
    expect(await request, isTrue);
    expect(controller.state.sequence, 5);
    expect(controller.state.taskStatus, 'cancelled');
  });

  testWidgets('重连快照失败时继续保持断线状态', (tester) async {
    final service = DelayedSnapshotService();
    final controller = ClassroomController(service);
    addTearDown(controller.dispose);
    controller.disconnect();
    await tester.pump();
    service.pending = Completer<SessionModel>();
    final request = controller.reconnect();
    await tester.pump();
    expect(controller.connected, isFalse);
    service.pending!.completeError(const ClassroomFailure('快照失败'));
    expect(await request, isFalse);
    expect(controller.connected, isFalse);
    service.pending = null;
    expect(await controller.reconnect(), isTrue);
    expect(controller.connected, isTrue);
  });

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
    // 课堂目标不再预填示例文本，先输入目标再开始课堂。
    await tester.enterText(find.byKey(const Key('task-target')), '认识桌面物体');
    await tester.tap(find.byKey(const Key('task-toggle')));
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
    // 错误就近显示在消息输入下方，而不是页面顶部横幅。
    expect(find.text('杯子是什么颜色？'), findsOneWidget);
    final Finder errorBox = find.byKey(const Key('field-error-box'));
    expect(errorBox, findsOneWidget);
    final double errorTop = tester.getTopLeft(errorBox).dy;
    final double inputTop = tester
        .getTopLeft(find.byKey(const Key('message-input')))
        .dy;
    expect(errorTop, greaterThan(inputTop));
    controller.selectScenario(DemoScenario.normal);
    await tester.pump();
    await tester.tap(find.byKey(const Key('send-message')));
    await advance(tester);
    expect(service.commands, contains('submit_text'));
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    service.dispose();
  });

  testWidgets('点击发送立即进入加载，老师回答事件到达才结束', (WidgetTester tester) async {
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await controller.createTask('认识物体');
    await advance(tester);
    expect(controller.awaitingReply, isFalse);

    // 调用瞬间（Future 未完成）即进入加载，不依赖服务端时长。
    final Future<bool> sent = controller.submitText('杯子是什么颜色？');
    expect(controller.awaitingReply, isTrue);

    expect(await sent, isTrue);
    // 提交命令已完成但回答未到：仍保持加载——由事件收口，非写死时长。
    expect(controller.awaitingReply, isTrue);

    // 回答延迟 4 step 后到达，加载随之结束。
    await advance(tester, 6);
    expect(controller.awaitingReply, isFalse);
    expect(
      controller.state.messages.any((ClassMessage m) => m.role == 'assistant'),
      isTrue,
    );
    // 排干回答后的播报链计时器，避免测试收尾时挂起 Timer。
    await advance(tester);
    service.dispose();
  });

  testWidgets('录音输入与文字一致：转写即加载，回答到达才结束', (WidgetTester tester) async {
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await controller.createTask('认识物体');
    await advance(tester);
    expect(controller.awaitingReply, isFalse);

    await controller.record();
    await tester.pump();
    // 录音中不加载（等同文字的打字阶段）。
    expect(controller.awaitingReply, isFalse);

    // 结束录音即「提交」：转写事件到达后立即进入加载。
    await controller.record();
    await tester.pump();
    expect(controller.awaitingReply, isTrue);

    // 转写产生学生提问，老师回答到达后收口。
    await advance(tester);
    expect(controller.awaitingReply, isFalse);
    expect(
      controller.state.messages.any((ClassMessage m) => m.role == 'student'),
      isTrue,
    );
    await advance(tester);
    service.dispose();
  });

  testWidgets('录音无有效语音时加载态收口，不空等回答', (WidgetTester tester) async {
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await controller.createTask('认识物体');
    await advance(tester);
    controller.selectScenario(DemoScenario.silence);
    await controller.record();
    await controller.record();
    await tester.pump();
    expect(controller.awaitingReply, isTrue);

    // 静音收口：没有学生提问就不会有回答，加载自行结束。
    await advance(tester);
    expect(controller.awaitingReply, isFalse);
    await advance(tester);
    service.dispose();
  });

  testWidgets('录音失败时把设备说明显示出来，不让失败静默', (WidgetTester tester) async {
    tester.view.physicalSize = const Size(1366, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
      recordTick: const Duration(seconds: 1),
      maxRecordingSeconds: 30,
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await tester.pumpWidget(
      MaterialApp(home: ClassroomPage(controller: controller)),
    );
    await tester.pump();
    await tester.enterText(find.byKey(const Key('task-target')), '认识桌面物体');
    await tester.tap(find.byKey(const Key('task-toggle')));
    await advance(tester);
    // 静音场景：转写结束但没有有效语音，设备端会给一句说明。
    controller.selectScenario(DemoScenario.silence);
    await tester.ensureVisible(find.byKey(const Key('record-button')));
    await tester.tap(find.byKey(const Key('record-button')));
    await advance(tester, 3);
    await tester.tap(find.byKey(const Key('record-button')));
    await advance(tester, 6);
    expect(find.byKey(const Key('device-note')), findsOneWidget);
    expect(find.text('未识别到有效语音，请重新录音。'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    service.dispose();
  });

  testWidgets('录音计时数字逐位滚动', (WidgetTester tester) async {
    tester.view.physicalSize = const Size(1366, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final MockClassroomService service = MockClassroomService(
      step: const Duration(milliseconds: 100),
      recordTick: const Duration(seconds: 1),
      maxRecordingSeconds: 30,
    );
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    await tester.pumpWidget(
      MaterialApp(home: ClassroomPage(controller: controller)),
    );
    await tester.pump();
    // 课堂目标不再预填示例文本，先输入目标再开始课堂。
    await tester.enterText(find.byKey(const Key('task-target')), '认识桌面物体');
    await tester.tap(find.byKey(const Key('task-toggle')));
    await advance(tester);
    await tester.ensureVisible(find.byKey(const Key('record-button')));
    await tester.tap(find.byKey(const Key('record-button')));
    await tester.pump(const Duration(milliseconds: 80));
    // 计时数字只在 record-timer 滚轮内查找（页面其他处有 1/2/3 步骤序号）。
    Finder digit(String d) => find.descendant(
      of: find.byKey(const Key('record-timer')),
      matching: find.text(d),
    );
    // 计时 00：两个数字格。
    expect(digit('0'), findsNWidgets(2));
    expect(digit('1'), findsNothing);

    // 1 秒整跳到 01，滚动进行中：新旧数字共存。
    await tester.pump(const Duration(milliseconds: 1050));
    await tester.pump(const Duration(milliseconds: 50));
    expect(digit('1'), findsOneWidget);
    expect(digit('0'), findsWidgets);

    await tester.pump(const Duration(milliseconds: 500));
    // 滚动结束：十位 0、个位 1。
    expect(digit('0'), findsOneWidget);
    expect(digit('1'), findsOneWidget);

    await tester.tap(find.byKey(const Key('record-button')));
    await advance(tester);
    await tester.pumpWidget(const SizedBox());
    service.dispose();
  });

  testWidgets('销毁自有页面时释放活动定时器和订阅', (WidgetTester tester) async {
    await tester.pumpWidget(const MaterialApp(home: ClassroomPage()));
    await tester.pump();
    // 课堂目标不再预填示例文本，先输入目标再开始课堂。
    await tester.enterText(find.byKey(const Key('task-target')), '认识桌面物体');
    await tester.tap(find.byKey(const Key('task-toggle')));
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

  testWidgets('首屏快照失败即标记未连接，恢复后重新连上', (WidgetTester tester) async {
    final FlakySnapshotService service = FlakySnapshotService();
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    // 服务不可达：初始化失败必须落成「未连接」，不能默认显示连接正常。
    expect(await controller.initialize(), isFalse);
    expect(controller.connected, isFalse);
    expect(controller.errorSource, 'connection');

    service.fail = false;
    expect(await controller.reconnect(), isTrue);
    expect(controller.connected, isTrue);
    expect(controller.error, isNull);
    service.dispose();
  });

  testWidgets('课堂目标为空时本地拦截，不伪造创建请求', (WidgetTester tester) async {
    final MockClassroomService service = MockClassroomService();
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    expect(await controller.createTask('   '), isFalse);
    expect(controller.errorSource, 'task');
    expect(controller.error, contains('课堂目标'));
    expect(controller.state.taskStatus, 'idle');
    expect(service.commands, isEmpty);
    service.dispose();
  });

  testWidgets('首屏连接结果回来前显示连接中，不误报连接正常', (WidgetTester tester) async {
    final DelayedSnapshotService service = DelayedSnapshotService();
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    service.pending = Completer<SessionModel>();
    final Future<bool> request = controller.initialize();
    await tester.pump();
    // 快照未回：既不是 connected 也不是 disconnected。
    expect(controller.connection, DeviceConnection.connecting);

    await tester.pumpWidget(
      MaterialApp(home: ClassroomPage(controller: controller)),
    );
    await tester.pump();
    // 头部与画面占位都处于连接中，不出现「连接正常」或「未连接」。
    expect(find.text('正在连接设备…'), findsWidgets);
    expect(find.text('正在获取设备数据，请稍候…'), findsOneWidget);
    expect(find.text('设备连接正常'), findsNothing);
    expect(find.text('未连接服务器'), findsNothing);

    service.pending!.completeError(const ClassroomFailure('设备不可达'));
    expect(await request, isFalse);
    await tester.pump();
    expect(controller.connection, DeviceConnection.disconnected);
    expect(find.text('未连接服务器'), findsWidgets);
    await tester.pumpWidget(const SizedBox());
    service.dispose();
  });

  testWidgets('未连接服务器时画面与观察显示未连接，不出现示例内容', (WidgetTester tester) async {
    tester.view.physicalSize = const Size(1366, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final MockClassroomService service = MockClassroomService();
    final ClassroomController controller = ClassroomController(service);
    addTearDown(controller.dispose);
    controller.disconnect();
    await tester.pumpWidget(
      MaterialApp(home: ClassroomPage(controller: controller)),
    );
    await tester.pump();
    expect(find.text('未连接服务器'), findsWidgets);
    expect(find.text('未连接服务器，暂无观察结果。'), findsOneWidget);
    expect(find.text('设备连接正常'), findsNothing);
    expect(find.text('设备摄像头'), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
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
      // 课堂目标不再预填示例文本，先输入目标再开始课堂。
      await tester.enterText(find.byKey(const Key('task-target')), '认识桌面物体');
      await tester.tap(find.byKey(const Key('task-toggle')));
      await advance(tester);
      expect(tester.takeException(), isNull);
      await tester.ensureVisible(find.byKey(const Key('task-toggle')));
      await tester.tap(find.byKey(const Key('task-toggle')));
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
