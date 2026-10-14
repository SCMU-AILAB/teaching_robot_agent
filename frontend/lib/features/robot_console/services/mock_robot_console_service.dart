// frontend/lib/features/robot_console/services/mock_robot_console_service.dart
import 'dart:async';

import '../models/robot_console_models.dart';
import 'robot_console_service.dart';

/// 内存控制台模拟器，所有定时事件均可取消，不调用 Python、网络或真实设备。
class MockRobotConsoleService implements RobotConsoleService {
  MockRobotConsoleService({
    this.step = const Duration(milliseconds: 900),
    this.recordTick = const Duration(seconds: 1),
    this.maxRecordingSeconds = 15,
  });

  /// 相邻两个状态推进之间的间隔。
  final Duration step;

  /// 录音计时的递进间隔。
  final Duration recordTick;

  /// 录音最长秒数，到达后自动结束并识别。
  final int maxRecordingSeconds;

  final StreamController<RobotSession> _events = StreamController.broadcast();
  final StreamController<bool> _connections = StreamController.broadcast();
  final List<Timer> _timers = [];
  final List<String> commands = [];
  RobotSession _state = const RobotSession();
  ConsoleScenario _scenario = ConsoleScenario.normal;
  Timer? _recordTimer;
  bool _online = true;
  bool _disposed = false;
  int _generation = 0;
  int _messageId = 0;
  int _actionId = 0;
  int _observationId = 0;
  int _taskId = 0;

  @override
  Stream<RobotSession> get events => _events.stream;

  @override
  Stream<bool> get connections => _connections.stream;

  void _requireOnline() {
    if (_disposed) throw const RobotConsoleFailure('控制台已关闭。');
    if (!_online) {
      throw const RobotConsoleFailure('连接已断开，请求尚未送达。请恢复连接后重试。');
    }
    if (_scenario == ConsoleScenario.requestFailure) {
      throw const RobotConsoleFailure('请求失败，当前操作未执行。请重试。');
    }
  }

  void _requireTask() {
    _requireOnline();
    if (!_state.taskActive) throw const RobotConsoleFailure('请先创建任务。');
  }

  void _emit(RobotSession state) {
    if (_disposed) return;
    _state = state.copyWith(sequence: _state.sequence + 1);
    // 离线时只更新内部状态，快照在重连后由 snapshot() 恢复。
    if (_online) _events.add(_state);
  }

  void _schedule(void Function() action, [int ticks = 1]) {
    final int generation = _generation;
    late final Timer timer;
    timer = Timer(step * ticks, () {
      _timers.remove(timer);
      if (!_disposed && generation == _generation) action();
    });
    _timers.add(timer);
  }

  void _cancelTimers() {
    _generation++;
    _recordTimer?.cancel();
    _recordTimer = null;
    for (final Timer timer in _timers) {
      timer.cancel();
    }
    _timers.clear();
  }

  @override
  Future<RobotSession> snapshot() async {
    _requireOnline();
    return _state;
  }

  @override
  Future<void> createTask(String target) async {
    _requireOnline();
    final String trimmed = target.trim();
    if (trimmed.isEmpty || trimmed.length > 2000) {
      throw const RobotConsoleFailure('请输入 1–2000 字的任务目标。');
    }
    if (_state.taskActive) throw const RobotConsoleFailure('请先结束当前任务。');
    commands.add('create_task');
    _cancelTimers();
    _emit(
      RobotSession(
        instanceId: _state.instanceId,
        taskId: ++_taskId,
        taskTarget: trimmed,
        taskStatus: 'running',
        maxRecordingSeconds: maxRecordingSeconds,
        messages: [
          ChatMessage(
            messageId: 'm-${++_messageId}',
            role: 'system',
            text: '机器人任务已就绪。',
          ),
        ],
        robot: _state.robot.copyWith(isMoving: false),
      ),
    );
  }

  @override
  Future<void> submitText(String text) async {
    _requireTask();
    final String trimmed = text.trim();
    if (trimmed.isEmpty || trimmed.length > 4000) {
      throw const RobotConsoleFailure('请输入 1–4000 字的内容。');
    }
    if (_state.processing || _state.audioBusy) {
      throw const RobotConsoleFailure('请等待当前处理结束。');
    }
    commands.add('submit_text');
    _submitInput(trimmed);
  }

  /// 追加用户消息并进入回答流程；公开提交与录音转写共用。
  void _submitInput(String text) {
    _emit(
      _state.copyWith(
        messages: [
          ..._state.messages,
          ChatMessage(
            messageId: 'm-${++_messageId}',
            role: 'user',
            text: text,
          ),
        ],
        note: '',
        processing: true,
      ),
    );
    _respond(text);
  }

  void _respond(String text) {
    _schedule(() {
      if (text.contains('热水')) {
        _answerAndSpeak(
          '热水壶里的水很烫，直接碰容易烫伤。要请大人帮忙倒热水，等晾温了再拿。'
          '来源：项目人工编写的安全教育示例；待负责人审核，非现场风险检测结论',
        );
      } else if (text.contains('看') || text.contains('观察') || text.contains('什么')) {
        _respondObservation();
      } else if (text.contains('转') ||
          text.contains('移动') ||
          text.contains('前进') ||
          text.contains('走')) {
        _respondMotion();
      } else {
        _answerAndSpeak(
          '好呀！你可以让我“看看桌上有什么”，我会观察并讲讲相关的安全知识；'
          '也可以继续问我问题，我们一起学习。',
        );
      }
    });
  }

  /// 观察分支：先完成观察，再按场景给出讲解并播报。
  void _respondObservation() {
    _runObservation(() {
      if (_scenario == ConsoleScenario.emptyObservation) {
        _answerAndSpeak(
          '画面中没有识别出明确物体，资料库没有相关条目。'
          '可以让我换个方向再看看，或者直接告诉我你看到了什么。',
        );
      } else {
        _answerAndSpeak(
          '我看到桌面上有电热水壶（中间）、马克杯（右侧）和插线板（左侧）。'
          '小提醒：热水壶刚烧完水很烫，要请大人帮忙，不要自己碰；'
          '插线板不能用手或金属物捅插孔，也不能用湿手摸。',
        );
      }
    });
  }

  /// 移动分支：动作完成不等于任务完成；移动使旧观察过期并强制重新观察。
  void _respondMotion() {
    final int actionId = ++_actionId;
    _emit(
      _state.copyWith(
        actions: [
          ..._state.actions,
          RobotAction(
            actionId: actionId,
            skillName: 'turn_relative',
            argsSummary: 'angle_rad=0.1, speed_rad_s=0.2',
            status: 'queued',
          ),
        ],
      ),
    );
    _schedule(() {
      _updateAction(actionId, 'running');
      _emit(
        _state.copyWith(
          robot: _state.robot.copyWith(
            isMoving: true,
            poseText: 'x=0.00 y=0.00 yaw=0.10',
          ),
        ),
      );
      _schedule(() {
        _updateAction(actionId, 'verifying');
        _schedule(() {
          final bool hadObservation = _state.observation != null;
          _updateAction(actionId, 'succeeded', resultSummary: '转向完成');
          _emit(
            _state.copyWith(
              robot: _state.robot.copyWith(isMoving: false),
              observation: _state.observation?.copyWith(stale: true),
            ),
          );
          if (hadObservation) {
            // 已有场景观察：移动终态先使旧观察失效，再强制重新观察后回答。
            _runObservation(
              () => _answerAndSpeak('已向左转 0.1 弧度，我重新观察了现在的场景。'),
            );
          } else {
            _answerAndSpeak('已向左转 0.1 弧度。需要我看看现在的场景吗？');
          }
        });
      });
    });
  }

  /// 观察状态链：queued → running → succeeded，结束后触发 [onDone]。
  void _runObservation([void Function()? onDone]) {
    _emit(
      _state.copyWith(
        observation: SceneObservation(
          observationId: 'obs-${++_observationId}',
          status: 'queued',
        ),
      ),
    );
    _schedule(() {
      final SceneObservation? current = _state.observation;
      if (current == null) return;
      _emit(_state.copyWith(observation: current.copyWith(status: 'running')));
      _schedule(() {
        _emit(_state.copyWith(observation: _buildSucceededObservation()));
        onDone?.call();
      });
    });
  }

  SceneObservation _buildSucceededObservation() {
    final double capturedAt = DateTime.now().millisecondsSinceEpoch / 1000.0;
    if (_scenario == ConsoleScenario.emptyObservation) {
      return SceneObservation(
        observationId: 'obs-${++_observationId}',
        status: 'succeeded',
        summary: '画面中未识别出明确物体。',
        capturedAt: capturedAt,
        modelId: 'simulated-vlm',
      );
    }
    const String source = '项目人工编写的安全教育示例；待负责人审核，非现场风险检测结论';
    return SceneObservation(
      observationId: 'obs-${++_observationId}',
      status: 'succeeded',
      summary: '桌面上有一个电热水壶、一个马克杯和一个插线板。',
      objects: const [
        SceneObject(objectId: 'obj-1', label: '电热水壶', position: '中间'),
        SceneObject(objectId: 'obj-2', label: '马克杯', position: '右侧'),
        SceneObject(objectId: 'obj-3', label: '插线板', position: '左侧'),
      ],
      relatedSafety: const [
        SafetyEntry(
          title: '热水与烫伤预防',
          explanation: '热水壶刚烧完水很烫，要请大人帮忙倒水，不要自己碰壶身和壶嘴。',
          source: source,
        ),
        SafetyEntry(
          title: '插座与用电安全',
          explanation: '不能用手或金属物捅插孔，也不能用湿手摸插线板和插头。',
          source: source,
        ),
      ],
      capturedAt: capturedAt,
      modelId: 'simulated-vlm',
    );
  }

  /// 追加 assistant 回答并串联 speak 动作链，结束后解除 processing。
  void _answerAndSpeak(String text) {
    final int actionId = ++_actionId;
    final String argsSummary =
        'text=${text.length > 20 ? text.substring(0, 20) : text}';
    _emit(
      _state.copyWith(
        messages: [
          ..._state.messages,
          ChatMessage(
            messageId: 'm-${++_messageId}',
            role: 'assistant',
            text: text,
            speechActionId: actionId,
          ),
        ],
        actions: [
          ..._state.actions,
          RobotAction(
            actionId: actionId,
            skillName: 'speak',
            argsSummary: argsSummary,
            status: 'queued',
          ),
        ],
        playbackStatus: 'synthesizing',
      ),
    );
    _schedule(() {
      if (_scenario == ConsoleScenario.speechFailure) {
        _updateAction(actionId, 'failed');
        _emit(
          _state.copyWith(
            playbackStatus: 'failed',
            processing: false,
            note: '语音合成失败，回答文字已保留。',
          ),
        );
        return;
      }
      _updateAction(actionId, 'running');
      _emit(_state.copyWith(playbackStatus: 'playing'));
      _schedule(() {
        _updateAction(actionId, 'succeeded');
        _emit(_state.copyWith(playbackStatus: 'completed', processing: false));
      });
    });
  }

  void _updateAction(
    int actionId,
    String status, {
    String? resultSummary,
    String? failure,
  }) {
    _emit(
      _state.copyWith(
        actions: [
          for (final RobotAction action in _state.actions)
            if (action.actionId == actionId)
              action.copyWith(
                status: status,
                resultSummary: resultSummary,
                failure: failure,
              )
            else
              action,
        ],
      ),
    );
  }

  @override
  Future<void> finishTask() async {
    _requireTask();
    if (_state.processing ||
        _state.audioBusy ||
        _state.actions.any((RobotAction action) => !action.terminal)) {
      throw const RobotConsoleFailure('任务仍在处理中，不能验收。');
    }
    commands.add('finish_task');
    _emit(
      _state.copyWith(taskStatus: 'completed', note: '任务已验收完成。'),
    );
  }

  @override
  Future<void> stopTask() async {
    _requireOnline();
    if (!_state.taskActive) return;
    commands.add('stop_task');
    _cancelTimers();
    _emit(
      _state.copyWith(
        taskStatus: 'cancelling',
        note: '停止请求已接受，正在确认资源释放。',
      ),
    );
    _schedule(
      () => _emit(
        _state.copyWith(
          taskStatus: 'cancelled',
          processing: false,
          recordingStatus:
              _state.recordingStatus == 'recording' ||
                  _state.recordingStatus == 'transcribing'
              ? 'cancelled'
              : _state.recordingStatus,
          playbackStatus: _state.playbackStatus == 'playing'
              ? 'stopped'
              : _state.playbackStatus == 'synthesizing'
              ? 'cancelled'
              : _state.playbackStatus,
          actions: [
            for (final RobotAction action in _state.actions)
              if (action.terminal)
                action
              else
                action.copyWith(status: 'cancelled'),
          ],
          note: '任务已停止，模拟资源已释放。',
        ),
      ),
      2,
    );
  }

  @override
  Future<void> startRecording() async {
    _requireTask();
    if (_state.processing || _state.audioBusy) {
      throw const RobotConsoleFailure('音频设备正在使用或任务处理中，请稍后。');
    }
    commands.add('start_recording');
    _emit(
      _state.copyWith(recordingStatus: 'recording', recordingSeconds: 0, note: ''),
    );
    _recordTimer = Timer.periodic(recordTick, (_) {
      final int seconds = _state.recordingSeconds + 1;
      _emit(_state.copyWith(recordingSeconds: seconds));
      if (seconds >= maxRecordingSeconds) _finishRecording();
    });
  }

  @override
  Future<void> finishRecording() async {
    _requireTask();
    if (_state.recordingStatus != 'recording') return;
    commands.add('finish_recording');
    _finishRecording();
  }

  void _finishRecording() {
    if (_state.recordingStatus != 'recording') return;
    _recordTimer?.cancel();
    _recordTimer = null;
    _emit(_state.copyWith(recordingStatus: 'transcribing'));
    _schedule(() {
      if (_scenario == ConsoleScenario.silence) {
        _emit(
          _state.copyWith(
            recordingStatus: 'completed',
            note: '未识别到有效语音，请重新录音或输入文字。',
          ),
        );
      } else if (_scenario == ConsoleScenario.asrFailure) {
        _emit(
          _state.copyWith(
            recordingStatus: 'failed',
            note: '识别失败，请重新录音或输入文字。',
          ),
        );
      } else {
        _emit(_state.copyWith(recordingStatus: 'completed'));
        _submitInput('帮我看看桌上有什么，注意安全。');
      }
    });
  }

  @override
  Future<void> refreshObservation() async {
    _requireTask();
    // 处理中或音频占用时拒绝刷新，避免与进行中的观察/播报链路交错；
    // 与 UI 的禁用状态保持一致。
    if (_state.processing || _state.audioBusy) {
      throw const RobotConsoleFailure('请等待当前处理结束。');
    }
    commands.add('refresh_observation');
    _runObservation();
  }

  @override
  void setScenario(ConsoleScenario scenario) => _scenario = scenario;

  @override
  void disconnect() {
    if (_disposed) return;
    _online = false;
    _connections.add(false);
  }

  @override
  Future<void> reconnect() async {
    if (_disposed) throw const RobotConsoleFailure('控制台已关闭。');
    _online = true;
    _connections.add(true);
  }

  @override
  void dispose() {
    if (_disposed) return;
    _disposed = true;
    _cancelTimers();
    unawaited(_events.close());
    unawaited(_connections.close());
  }
}
