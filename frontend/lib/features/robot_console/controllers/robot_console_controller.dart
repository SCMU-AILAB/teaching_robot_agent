// frontend/lib/features/robot_console/controllers/robot_console_controller.dart
import 'dart:async';

import 'package:flutter/foundation.dart';

import '../models/robot_console_models.dart';
import '../services/robot_console_service.dart';

/// 协调机器人控制台页面的命令、快照恢复与事件去重。
class RobotConsoleController extends ChangeNotifier {
  RobotConsoleController(this._service) {
    _eventSubscription = _service.events.listen(
      applyEvent,
      onError: _onStreamError,
      onDone: () => _onConnection(false),
    );
    _connectionSubscription = _service.connections.listen(_onConnection);
  }

  final RobotConsoleService _service;
  late final StreamSubscription<RobotSession> _eventSubscription;
  late final StreamSubscription<bool> _connectionSubscription;
  RobotSession state = const RobotSession();
  bool connected = true;
  bool busy = false;
  String? error;
  ConsoleScenario scenario = ConsoleScenario.normal;
  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  void _onConnection(bool value) {
    if (!value) connected = false;
    if (!value) error = '连接已断开，当前显示最后一次状态。停止请求尚未送达。';
    _notify();
  }

  void _onStreamError(Object failure) => _onConnection(false);

  /// 应用事件：不同实例视为服务重启，旧序号事件直接忽略。
  void applyEvent(RobotSession event) {
    if (_disposed) return;
    if (event.instanceId != state.instanceId) {
      connected = false;
      error = '服务实例已变化，请重新连接并恢复快照。';
      _notify();
      return;
    }
    if (event.sequence <= state.sequence) return;
    state = event;
    _notify();
  }

  Future<bool> _run(Future<void> Function() command) async {
    if (_disposed || busy) return false;
    busy = true;
    error = null;
    _notify();
    try {
      await command();
      return true;
    } on RobotConsoleFailure catch (failure) {
      error = failure.message;
      return false;
    } catch (_) {
      error = '操作失败，请重试。';
      return false;
    } finally {
      busy = false;
      _notify();
    }
  }

  /// 同实例快照不得覆盖已经收到的更新事件。
  void _acceptSnapshot(RobotSession snapshot) {
    if (_disposed) return;
    if (snapshot.instanceId != state.instanceId ||
        snapshot.sequence >= state.sequence) {
      state = snapshot;
    }
  }

  Future<bool> initialize() => _run(() async {
    _acceptSnapshot(await _service.snapshot());
  });

  Future<bool> createTask(String target) =>
      _run(() => _service.createTask(target));

  Future<bool> submitText(String text) => _run(() => _service.submitText(text));

  Future<bool> finishTask() => _run(_service.finishTask);

  /// 录音按钮按当前状态切换开始/结束。
  Future<bool> record() => _run(
    () => state.recordingStatus == 'recording'
        ? _service.finishRecording()
        : _service.startRecording(),
  );

  Future<bool> refresh() => _run(_service.refreshObservation);

  /// 停止绕过普通命令的忙碌标记，失败时保留最后确认的任务状态。
  Future<void> stop() async {
    try {
      await _service.stopTask();
      error = null;
    } on RobotConsoleFailure catch (failure) {
      error = failure.message;
    } catch (_) {
      error = '停止请求失败，尚未确认设备停止。请重试。';
    }
    _notify();
  }

  /// 先恢复连接，再恢复快照；快照成功才标记在线。
  Future<bool> reconnect() => _run(() async {
    await _service.reconnect();
    _acceptSnapshot(await _service.snapshot());
    if (!_disposed) connected = true;
  });

  void selectScenario(ConsoleScenario value) {
    scenario = value;
    _service.setScenario(value);
    error = null;
    _notify();
  }

  void disconnect() => _service.disconnect();

  @override
  void dispose() {
    _disposed = true;
    unawaited(_eventSubscription.cancel());
    unawaited(_connectionSubscription.cancel());
    _service.dispose();
    super.dispose();
  }
}
