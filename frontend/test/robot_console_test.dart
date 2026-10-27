// frontend/test/robot_console_test.dart
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/features/robot_console/controllers/robot_console_controller.dart';
import 'package:teaching_robot_frontend/features/robot_console/models/robot_console_models.dart';
import 'package:teaching_robot_frontend/features/robot_console/pages/robot_console_page.dart';
import 'package:teaching_robot_frontend/features/robot_console/services/mock_robot_console_service.dart';
import 'package:teaching_robot_frontend/features/robot_console/theme/console_theme.dart';

/// 推进 Mock 定时链路（step=100ms 时每个 pump 前进一拍）。
Future<void> advance(WidgetTester tester, [int ticks = 6]) async {
  for (int index = 0; index < ticks; index++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

/// 构造加速 Mock 的控制器，测试结束自动释放。
RobotConsoleController buildController(MockRobotConsoleService service) {
  final controller = RobotConsoleController(service);
  addTearDown(controller.dispose);
  return controller;
}

/// 控制快照返回时机，复现事件先到而旧快照迟到的顺序。
class DelayedSnapshotService extends MockRobotConsoleService {
  Completer<RobotSession>? pending;

  @override
  Future<RobotSession> snapshot() => pending?.future ?? super.snapshot();
}

void main() {
  group('任务生命周期', () {
    testWidgets('初始状态无任务', (tester) async {
      final controller = buildController(MockRobotConsoleService());
      await controller.initialize();
      expect(controller.state.hasTask, isFalse);
      expect(controller.state.taskStatus, 'idle');
      expect(controller.state.messages, isEmpty);
    });

    testWidgets('创建任务后进入 running 并有系统消息', (tester) async {
      final controller = buildController(MockRobotConsoleService());
      await controller.createTask('看看桌上有什么');
      expect(controller.state.taskStatus, 'running');
      expect(controller.state.taskId, 1);
      expect(controller.state.taskTarget, '看看桌上有什么');
      expect(controller.state.messages.single.role, 'system');
      expect(controller.state.messages.single.text, '机器人任务已就绪。');
    });

    testWidgets('活动任务重复创建被拒绝', (tester) async {
      final controller = buildController(MockRobotConsoleService());
      await controller.createTask('任务一');
      expect(await controller.createTask('任务二'), isFalse);
      expect(controller.error, '请先结束当前任务。');
    });

    testWidgets('处理中不能验收，回答链结束后可以验收', (tester) async {
      final controller = buildController(
        MockRobotConsoleService(step: const Duration(milliseconds: 100)),
      );
      await controller.createTask('闲聊');
      await controller.submitText('你好');
      // 处理链未结束，验收必须被拒绝。
      expect(await controller.finishTask(), isFalse);
      expect(controller.error, '任务仍在处理中，不能验收。');
      await advance(tester, 5);
      expect(controller.state.processing, isFalse);
      expect(await controller.finishTask(), isTrue);
      expect(controller.state.taskStatus, 'completed');
      expect(controller.state.note, '任务已验收完成。');
    });

    testWidgets('停止先 cancelling 后 cancelled，未终态动作转为取消', (tester) async {
      final controller = buildController(
        MockRobotConsoleService(step: const Duration(milliseconds: 100)),
      );
      await controller.createTask('左转一下');
      await controller.submitText('左转一下');
      // 推进一拍让动作进入 queued。
      await tester.pump(const Duration(milliseconds: 100));
      expect(controller.state.actions.single.status, 'queued');
      await controller.stop();
      expect(controller.state.taskStatus, 'cancelling');
      await advance(tester, 4);
      expect(controller.state.taskStatus, 'cancelled');
      expect(controller.state.actions.single.status, 'cancelled');
      expect(controller.state.actions.single.terminal, isTrue);
    });
  });

  group('对话', () {
    testWidgets('空文本禁止提交', (tester) async {
      final controller = buildController(MockRobotConsoleService());
      await controller.createTask('闲聊');
      expect(await controller.submitText('   '), isFalse);
      expect(controller.error, '请输入 1–4000 字的内容。');
      expect(controller.state.messages, hasLength(1));
    });

    testWidgets('消息顺序正确且追问属于同一任务', (tester) async {
      final controller = buildController(
        MockRobotConsoleService(step: const Duration(milliseconds: 100)),
      );
      await controller.createTask('闲聊');
      await controller.submitText('你好');
      await advance(tester, 5);
      await controller.submitText('再见');
      await advance(tester, 5);
      final messages = controller.state.messages;
      expect(messages, hasLength(5));
      expect(messages[0].role, 'system');
      expect(messages[1].role, 'user');
      expect(messages[1].text, '你好');
      expect(messages[2].role, 'assistant');
      expect(messages[3].role, 'user');
      expect(messages[3].text, '再见');
      expect(messages[4].role, 'assistant');
      // 连续追问没有创建新任务。
      expect(controller.state.taskId, 1);
      expect(controller.state.taskStatus, 'running');
    });

    testWidgets('处理中再次提交被拒绝', (tester) async {
      final controller = buildController(
        MockRobotConsoleService(step: const Duration(milliseconds: 100)),
      );
      await controller.createTask('闲聊');
      await controller.submitText('你好');
      expect(await controller.submitText('还没好'), isFalse);
      expect(controller.error, '请等待当前处理结束。');
      // 走完回答链，测试结束时不遗留模拟定时器。
      await advance(tester, 6);
    });
  });

  group('语音', () {
    testWidgets('结束录音后自动提交转写并触发观察回答', (tester) async {
      final controller = buildController(
        MockRobotConsoleService(
          step: const Duration(milliseconds: 100),
          recordTick: const Duration(milliseconds: 100),
        ),
      );
      await controller.createTask('看看桌上有什么');
      expect(await controller.record(), isTrue);
      expect(controller.state.recordingStatus, 'recording');
      await tester.pump(const Duration(milliseconds: 300));
      expect(controller.state.recordingSeconds, greaterThanOrEqualTo(2));
      expect(await controller.record(), isTrue); // 录音中再次点击 = 结束
      expect(controller.state.recordingStatus, 'transcribing');
      await advance(tester, 8);
      expect(controller.state.recordingStatus, 'completed');
      expect(
        controller.state.messages.any(
          (message) =>
              message.role == 'user' && message.text == '帮我看看桌上有什么，注意安全。',
        ),
        isTrue,
      );
      expect(controller.state.observation?.status, 'succeeded');
      expect(controller.state.playbackStatus, 'completed');
      expect(controller.state.processing, isFalse);
    });

    testWidgets('无有效语音时给出空结果提示', (tester) async {
      final service = MockRobotConsoleService(
        step: const Duration(milliseconds: 100),
        recordTick: const Duration(milliseconds: 100),
      );
      service.setScenario(ConsoleScenario.silence);
      final controller = buildController(service);
      await controller.createTask('闲聊');
      await controller.record();
      await controller.record();
      await advance(tester, 3);
      expect(controller.state.recordingStatus, 'completed');
      expect(controller.state.note, '未识别到有效语音，请重新录音或输入文字。');
      // 没有转写被提交为输入。
      expect(
        controller.state.messages.every((message) => message.role != 'user'),
        isTrue,
      );
    });

    testWidgets('识别失败时进入 failed 并提示', (tester) async {
      final service = MockRobotConsoleService(
        step: const Duration(milliseconds: 100),
        recordTick: const Duration(milliseconds: 100),
      );
      service.setScenario(ConsoleScenario.asrFailure);
      final controller = buildController(service);
      await controller.createTask('闲聊');
      await controller.record();
      await controller.record();
      await advance(tester, 3);
      expect(controller.state.recordingStatus, 'failed');
      expect(controller.state.note, '识别失败，请重新录音或输入文字。');
    });

    testWidgets('录音达到上限自动结束并识别', (tester) async {
      final controller = buildController(
        MockRobotConsoleService(
          step: const Duration(milliseconds: 100),
          recordTick: const Duration(milliseconds: 100),
          maxRecordingSeconds: 3,
        ),
      );
      await controller.createTask('看看桌上有什么');
      await controller.record();
      // 3 秒（测试中 3 拍）后自动结束，再走识别与回答链。
      await advance(tester, 10);
      expect(controller.state.recordingStatus, 'completed');
      expect(controller.state.maxRecordingSeconds, 3);
      expect(
        controller.state.messages.any(
          (message) =>
              message.role == 'user' && message.text == '帮我看看桌上有什么，注意安全。',
        ),
        isTrue,
      );
    });

    testWidgets('播放中开始录音被拒绝（录音播报互斥）', (tester) async {
      final controller = buildController(
        MockRobotConsoleService(step: const Duration(milliseconds: 100)),
      );
      await controller.createTask('闲聊');
      await controller.submitText('你好');
      // 推进两拍进入播放中。
      await advance(tester, 2);
      expect(controller.state.playbackStatus, 'playing');
      expect(await controller.record(), isFalse);
      expect(controller.error, '音频设备正在使用或任务处理中，请稍后。');
      // 走完回答链，测试结束时不遗留模拟定时器。
      await advance(tester, 6);
    });
  });

  group('场景观察', () {
    testWidgets('提问触发观察成功并返回物体与安全资料', (tester) async {
      final controller = buildController(
        MockRobotConsoleService(step: const Duration(milliseconds: 100)),
      );
      await controller.createTask('看看桌上有什么');
      await controller.submitText('看看桌上有什么');
      await advance(tester, 6);
      final observation = controller.state.observation;
      expect(observation, isNotNull);
      expect(observation!.status, 'succeeded');
      expect(observation.objects, hasLength(3));
      expect(observation.relatedSafety, hasLength(2));
      expect(observation.simulated, isTrue);
      expect(observation.modelId, 'simulated-vlm');
      expect(observation.stale, isFalse);
      expect(controller.state.playbackStatus, 'completed');
    });

    testWidgets('空观察场景返回无物体与无资料提示', (tester) async {
      final service = MockRobotConsoleService(
        step: const Duration(milliseconds: 100),
      );
      service.setScenario(ConsoleScenario.emptyObservation);
      final controller = buildController(service);
      await controller.createTask('看看桌上有什么');
      await controller.submitText('看看桌上有什么');
      await advance(tester, 6);
      final observation = controller.state.observation;
      expect(observation!.objects, isEmpty);
      expect(observation.relatedSafety, isEmpty);
      expect(
        controller.state.messages.any(
          (message) =>
              message.role == 'assistant' && message.text.contains('资料库没有相关条目'),
        ),
        isTrue,
      );
    });

    testWidgets('移动后强制重新观察并产生新观察结果', (tester) async {
      final controller = buildController(
        MockRobotConsoleService(step: const Duration(milliseconds: 100)),
      );
      await controller.createTask('看看桌上有什么');
      await controller.submitText('看看桌上有什么');
      await advance(tester, 6);
      final firstId = controller.state.observation!.observationId;
      await controller.submitText('左转一下');
      await advance(tester, 10);
      // 动作成功后旧观察失效，宿主强制重新观察。
      final motion = controller.state.actions.firstWhere(
        (action) => action.skillName == 'turn_relative',
      );
      expect(motion.status, 'succeeded');
      expect(controller.state.robot.isMoving, isFalse);
      expect(controller.state.robot.poseText, 'x=0.00 y=0.00 yaw=0.10');
      final second = controller.state.observation!;
      expect(second.observationId, isNot(firstId));
      expect(second.status, 'succeeded');
      expect(second.stale, isFalse);
    });

    testWidgets('处理中刷新观察被拒绝', (tester) async {
      final controller = buildController(
        MockRobotConsoleService(step: const Duration(milliseconds: 100)),
      );
      await controller.createTask('闲聊');
      await controller.submitText('你好');
      expect(await controller.refresh(), isFalse);
      expect(controller.error, '请等待当前处理结束。');
      // 走完回答链，测试结束时不遗留模拟定时器。
      await advance(tester, 6);
    });
  });

  group('动作', () {
    testWidgets('转向动作按 queued/running/verifying/succeeded 流转', (tester) async {
      final controller = buildController(
        MockRobotConsoleService(step: const Duration(milliseconds: 100)),
      );
      await controller.createTask('左转一下');
      await controller.submitText('左转一下');
      RobotAction actionOfState() => controller.state.actions.singleWhere(
        (action) => action.skillName == 'turn_relative',
      );
      await tester.pump(const Duration(milliseconds: 100));
      expect(actionOfState().status, 'queued');
      await tester.pump(const Duration(milliseconds: 100));
      expect(actionOfState().status, 'running');
      expect(controller.state.robot.isMoving, isTrue);
      await tester.pump(const Duration(milliseconds: 100));
      expect(actionOfState().status, 'verifying');
      await tester.pump(const Duration(milliseconds: 100));
      expect(actionOfState().status, 'succeeded');
      expect(actionOfState().resultSummary, '转向完成');
      expect(controller.state.robot.isMoving, isFalse);
      // 无历史观察时不强制重新观察，直接回答并播报。
      await advance(tester, 3);
      expect(controller.state.playbackStatus, 'completed');
      expect(controller.state.processing, isFalse);
    });

    testWidgets('合成失败时播报动作失败且文字回答保留', (tester) async {
      final service = MockRobotConsoleService(
        step: const Duration(milliseconds: 100),
      );
      service.setScenario(ConsoleScenario.speechFailure);
      final controller = buildController(service);
      await controller.createTask('闲聊');
      await controller.submitText('你好');
      await advance(tester, 5);
      final speak = controller.state.actions.singleWhere(
        (action) => action.skillName == 'speak',
      );
      expect(speak.status, 'failed');
      expect(controller.state.playbackStatus, 'failed');
      expect(controller.state.note, '语音合成失败，回答文字已保留。');
      // 回答文字保留为 assistant 消息。
      expect(
        controller.state.messages.any((message) => message.role == 'assistant'),
        isTrue,
      );
      // 处理已结束，允许验收。
      expect(await controller.finishTask(), isTrue);
    });
  });

  group('连接与快照', () {
    testWidgets('断开显示错误，重连完成快照恢复后才在线', (tester) async {
      final service = DelayedSnapshotService();
      final controller = buildController(service);
      await controller.createTask('闲聊');
      final int sequence = controller.state.sequence;
      controller.disconnect();
      await tester.pump();
      expect(controller.connected, isFalse);
      expect(controller.error, isNotNull);
      service.pending = Completer<RobotSession>();
      final Future<bool> request = controller.reconnect();
      await tester.pump();
      // 快照尚未恢复，不能显示在线。
      expect(controller.connected, isFalse);
      service.pending!.complete(
        RobotSession(sequence: sequence + 10, taskStatus: 'running'),
      );
      expect(await request, isTrue);
      expect(controller.connected, isTrue);
      expect(controller.state.sequence, sequence + 10);
    });

    testWidgets('旧事件与迟到快照不覆盖新状态', (tester) async {
      final service = DelayedSnapshotService();
      final controller = buildController(service);
      controller.applyEvent(
        const RobotSession(sequence: 5, taskStatus: 'cancelled'),
      );
      expect(controller.state.sequence, 5);
      expect(controller.state.taskStatus, 'cancelled');
      // 旧序号事件直接忽略。
      controller.applyEvent(
        const RobotSession(sequence: 3, taskStatus: 'running'),
      );
      expect(controller.state.sequence, 5);
      // 实例变化视为服务重启。
      controller.applyEvent(
        const RobotSession(
          instanceId: 'another-instance',
          sequence: 9,
          taskStatus: 'running',
        ),
      );
      expect(controller.connected, isFalse);
      expect(controller.error, '服务实例已变化，请重新连接并恢复快照。');
      // 迟到的旧快照不得覆盖已应用的新事件。
      service.pending = Completer<RobotSession>();
      final Future<bool> request = controller.initialize();
      service.pending!.complete(
        const RobotSession(sequence: 2, taskStatus: 'running'),
      );
      expect(await request, isTrue);
      expect(controller.state.sequence, 5);
      expect(controller.state.taskStatus, 'cancelled');
    });
  });

  group('布局', () {
    const keys = [
      'status_bar',
      'task_panel',
      'conversation_panel',
      'observation_panel',
      'action_panel',
    ];

    testWidgets('宽屏三栏与窄屏纵向布局下五个区域均可见', (tester) async {
      final service = MockRobotConsoleService(
        step: const Duration(milliseconds: 100),
      );
      await tester.binding.setSurfaceSize(const Size(1400, 900));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      await tester.pumpWidget(
        MaterialApp(
          theme: buildConsoleTheme(),
          home: RobotConsolePage(service: service),
        ),
      );
      await tester.pump(const Duration(milliseconds: 100));
      for (final key in keys) {
        expect(find.byKey(Key(key)), findsOneWidget, reason: '宽屏布局缺少区域 $key');
      }
      // 宽屏进入三栏：发送消息验证交互可用。
      await tester.enterText(find.byKey(const Key('target_input')), '看看桌上有什么');
      await tester.tap(find.byKey(const Key('create_button')));
      await tester.pump(const Duration(milliseconds: 100));
      expect(find.text('机器人任务已就绪。'), findsOneWidget);

      // 窄屏切换为纵向，五个区域仍然可用。
      await tester.binding.setSurfaceSize(const Size(500, 900));
      await tester.pump();
      for (final key in keys) {
        expect(find.byKey(Key(key)), findsOneWidget, reason: '窄屏布局缺少区域 $key');
      }
    });

    testWidgets('页面发送文字后用户消息上屏', (tester) async {
      final service = MockRobotConsoleService(
        step: const Duration(milliseconds: 100),
      );
      await tester.binding.setSurfaceSize(const Size(1400, 900));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      await tester.pumpWidget(
        MaterialApp(
          theme: buildConsoleTheme(),
          home: RobotConsolePage(service: service),
        ),
      );
      await tester.pump(const Duration(milliseconds: 100));
      await tester.enterText(find.byKey(const Key('target_input')), '闲聊');
      await tester.tap(find.byKey(const Key('create_button')));
      await tester.pump(const Duration(milliseconds: 100));
      await tester.enterText(find.byKey(const Key('message_input')), '你好');
      await tester.tap(find.byKey(const Key('send_button')));
      await tester.pump(const Duration(milliseconds: 100));
      expect(find.text('你好'), findsOneWidget);
    });
  });
}
