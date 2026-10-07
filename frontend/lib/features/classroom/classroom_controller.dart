// frontend/lib/features/classroom/classroom_controller.dart
import 'dart:async';

import 'package:flutter/foundation.dart';

import 'classroom_service.dart';
import 'session_model.dart';

/// 协调页面命令、快照恢复与事件去重，不读取 Runtime 事件队列。
class ClassroomController extends ChangeNotifier {
  ClassroomController(this._service) {
    _eventSubscription = _service.events.listen(
      applyEvent,
      onError: _onStreamError,
      onDone: () => _onConnection(false),
    );
    _connectionSubscription = _service.connections.listen(_onConnection);
  }

  final ClassroomService _service;
  late final StreamSubscription<SessionModel> _eventSubscription;
  late final StreamSubscription<bool> _connectionSubscription;
  SessionModel state = const SessionModel();
  bool connected = true;
  bool busy = false;
  String? error;
  DemoScenario scenario = DemoScenario.normal;
  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  void _onConnection(bool value) {
    connected = value;
    if (!value) error = '连接已断开，当前显示最后一次状态。停止请求尚未送达。';
    _notify();
  }

  void _onStreamError(Object failure) => _onConnection(false);

  void applyEvent(SessionModel event) {
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
    } on ClassroomFailure catch (failure) {
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

  Future<bool> initialize() => _run(() async {
    state = await _service.snapshot();
  });
  Future<bool> createTask(String target) =>
      _run(() => _service.createTask(target));
  Future<bool> submitText(String text) => _run(() => _service.submitText(text));
  Future<bool> record() => _run(
    () => state.recording == 'recording'
        ? _service.finishRecording()
        : _service.startRecording(),
  );
  Future<bool> refresh() => _run(_service.refreshObservation);

  /// 停止绕过普通命令的忙碌标记，失败时保留最后确认的任务状态。
  Future<void> stop() async {
    try {
      await _service.stopTask();
      error = null;
    } on ClassroomFailure catch (failure) {
      error = failure.message;
    } catch (_) {
      error = '停止请求失败，尚未确认设备停止。请重试。';
    }
    _notify();
  }

  Future<bool> reconnect() => _run(() async {
    await _service.reconnect();
    state = await _service.snapshot();
    connected = true;
  });

  void selectScenario(DemoScenario value) {
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
