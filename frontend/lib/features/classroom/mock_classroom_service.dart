// frontend/lib/features/classroom/mock_classroom_service.dart
import 'dart:async';

import 'classroom_service.dart';
import 'session_model.dart';

/// 内存课堂模拟器，所有定时事件均可取消，不调用 Python 或网络接口。
class MockClassroomService implements ClassroomService {
  MockClassroomService({
    this.step = const Duration(milliseconds: 900),
    this.recordTick = const Duration(seconds: 1),
    this.maxRecordingSeconds = 15,
  });

  final Duration step;
  final Duration recordTick;
  final int maxRecordingSeconds;
  final StreamController<SessionModel> _events = StreamController.broadcast();
  final StreamController<bool> _connections = StreamController.broadcast();
  final List<Timer> _timers = [];
  final List<String> commands = [];
  SessionModel _state = const SessionModel();
  DemoScenario _scenario = DemoScenario.normal;
  Timer? _recordTimer;
  bool _online = true;
  bool _disposed = false;
  bool _initializing = false;
  int _generation = 0;
  int _messageId = 0;
  int _taskId = 0;

  @override
  Stream<SessionModel> get events => _events.stream;
  @override
  Stream<bool> get connections => _connections.stream;

  void _requireOnline() {
    if (_disposed) throw const ClassroomFailure('课堂已关闭。');
    if (!_online) throw const ClassroomFailure('连接已断开，请求尚未送达。请恢复连接后重试。');
    if (_scenario == DemoScenario.requestFailure) {
      throw const ClassroomFailure('请求失败，当前操作未执行。请重试。');
    }
  }

  void _requireTask() {
    _requireOnline();
    if (_state.taskStatus != 'running') {
      throw const ClassroomFailure('请先开始一节课堂。');
    }
  }

  void _emit(SessionModel state) {
    if (_disposed) return;
    _state = state.copyWith(sequence: _state.sequence + 1);
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
  Future<SessionModel> snapshot() async {
    _requireOnline();
    return _state;
  }

  @override
  Future<void> createTask(String target) async {
    _requireOnline();
    if (target.trim().isEmpty || target.length > 2000) {
      throw const ClassroomFailure('请输入 1–2000 字的课堂目标。');
    }
    if (_state.active) throw const ClassroomFailure('请先停止当前课堂。');
    commands.add('create_task');
    _cancelTimers();
    _initializing = true;
    final int sequence = _state.sequence;
    _emit(
      SessionModel(
        taskId: ++_taskId,
        sequence: sequence,
        target: target.trim(),
        taskStatus: 'running',
        stage: 'explaining',
        observation: '正在观察桌面物体…',
        actions: const [ClassAction(1, '观察桌面', 'running')],
      ),
    );
    _schedule(() {
      _initializing = false;
      _emit(
        _state.copyWith(
          observed: true,
          observation: '桌面上有一个绿色杯子、一本蓝色书和一个橙色方块。杯子在书的左侧。',
          actions: const [ClassAction(1, '观察桌面', 'succeeded')],
        ),
      );
      _answer('我们一起观察桌面。你看到了什么？可以描述物体的颜色和位置。');
    });
  }

  void _answer(String text) {
    _emit(
      _state.copyWith(
        messages: [
          ..._state.messages,
          ClassMessage(++_messageId, 'assistant', text),
        ],
        stage: 'explaining',
        playback: 'synthesizing',
        actions: [
          ..._state.actions,
          ClassAction(_messageId + 100, '回答播报', 'queued'),
        ],
      ),
    );
    _schedule(() {
      if (_scenario == DemoScenario.synthesisFailure) {
        _updateSpeechAction('failed');
        _emit(
          _state.copyWith(
            playback: 'failed',
            stage: 'awaiting_answer',
            note: '语音合成失败，回答文本已保留。',
          ),
        );
        return;
      }
      _updateSpeechAction('running');
      _emit(_state.copyWith(playback: 'playing'));
      _schedule(() {
        _updateSpeechAction('succeeded');
        _emit(_state.copyWith(playback: 'completed', stage: 'awaiting_answer'));
      }, 3);
    });
  }

  void _updateSpeechAction(String status) {
    if (_state.actions.isEmpty) return;
    final ClassAction last = _state.actions.last;
    _emit(
      _state.copyWith(
        actions: [
          ..._state.actions.take(_state.actions.length - 1),
          ClassAction(last.id, last.label, status),
        ],
      ),
    );
  }

  bool get _audioBusy =>
      _initializing ||
      _state.recording == 'recording' ||
      _state.recording == 'transcribing' ||
      _state.playback == 'synthesizing' ||
      _state.playback == 'playing';

  @override
  Future<void> submitText(String text) async {
    _requireTask();
    if (text.trim().isEmpty || text.length > 4000) {
      throw const ClassroomFailure('请输入 1–4000 字的内容。');
    }
    if (_audioBusy) throw const ClassroomFailure('请等待当前语音交互结束。');
    commands.add('submit_text');
    _submitInput(text.trim());
  }

  void _submitInput(String text) {
    _emit(
      _state.copyWith(
        messages: [
          ..._state.messages,
          ClassMessage(++_messageId, 'student', text),
        ],
        stage: 'feedback',
        note: '',
      ),
    );
    _answer('收到你的描述：“$text”。在这张示例图中，绿色杯子在蓝色书的左边。再找一找橙色方块在哪里。');
  }

  @override
  Future<void> startRecording() async {
    _requireTask();
    if (_audioBusy) throw const ClassroomFailure('音频设备正在使用，请稍后录音。');
    commands.add('start_recording');
    _emit(
      _state.copyWith(recording: 'recording', recordingSeconds: 0, note: ''),
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
    if (_state.recording != 'recording') return;
    commands.add('finish_recording');
    _finishRecording();
  }

  void _finishRecording() {
    if (_state.recording != 'recording') return;
    _recordTimer?.cancel();
    _recordTimer = null;
    _emit(_state.copyWith(recording: 'transcribing'));
    _schedule(() {
      if (_scenario == DemoScenario.asrFailure) {
        _emit(_state.copyWith(recording: 'failed', note: '识别失败，请重新录音或输入文字。'));
      } else if (_scenario == DemoScenario.silence) {
        _emit(_state.copyWith(recording: 'completed', note: '未识别到有效语音，请重新录音。'));
      } else {
        _emit(_state.copyWith(recording: 'completed'));
        _submitInput('我看到了一个绿色杯子。');
      }
    });
  }

  @override
  Future<void> stopTask() async {
    _requireOnline();
    if (!_state.active) return;
    commands.add('stop_task');
    _cancelTimers();
    _emit(_state.copyWith(taskStatus: 'cancelling', note: '停止请求已接受，正在确认资源释放。'));
    _schedule(
      () => _emit(
        _state.copyWith(
          taskStatus: 'cancelled',
          recording:
              _state.recording == 'recording' ||
                  _state.recording == 'transcribing'
              ? 'cancelled'
              : _state.recording,
          playback: _state.playback == 'playing'
              ? 'stopped'
              : _state.playback == 'synthesizing'
              ? 'cancelled'
              : _state.playback,
          actions: _state.actions
              .map(
                (ClassAction action) => ClassAction(
                  action.id,
                  action.label,
                  ['running', 'queued', 'verifying'].contains(action.status)
                      ? 'cancelled'
                      : action.status,
                ),
              )
              .toList(),
          note: '课堂已停止，模拟资源已释放。',
        ),
      ),
    );
  }

  @override
  Future<void> refreshObservation() async {
    _requireTask();
    commands.add('refresh_observation');
    _emit(
      _state.copyWith(
        observed: true,
        stale: false,
        observation: '桌面上有一个绿色杯子、一本蓝色书和一个橙色方块。杯子在书的左侧。',
      ),
    );
  }

  @override
  void setScenario(DemoScenario scenario) => _scenario = scenario;

  @override
  void disconnect() {
    if (_disposed) return;
    _online = false;
    _connections.add(false);
  }

  @override
  Future<void> reconnect() async {
    if (_disposed) throw const ClassroomFailure('课堂已关闭。');
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
