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

  /// 发送后等待老师回答的加载态：点击发送即置位，收到老师消息事件才清除。
  ///
  /// 完全事件驱动、不含任何写死时长——后期接入真实推理时，回答以事件
  /// 形式经 [applyEvent] 到达，置位/清除时机无需改动。
  bool awaitingReply = false;

  /// 本次等待的基线消息 id：只有 id 更大的老师消息才算本次回复。
  int _replyFloorId = 0;

  /// 错误归属的控件：task / message / record / send / connection。
  ///
  /// 页面据此把小错误框放到对应控件旁，而不是整页顶部横幅。
  String? errorSource;
  DemoScenario scenario = DemoScenario.normal;
  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  /// 记录一次失败并标注来源控件。
  void _fail(String message, String source) {
    error = message;
    errorSource = source;
  }

  void _onConnection(bool value) {
    connected = value;
    if (value) {
      _clearConnectionError();
    } else {
      // 断线后回复无法送达，结束加载态。
      awaitingReply = false;
      _fail('未连接服务器，当前显示最后一次状态。停止请求尚未送达。', 'connection');
    }
    _notify();
  }

  /// 恢复连接后清掉上一条连接级错误，避免成功后仍显示断线提示。
  void _clearConnectionError() {
    if (errorSource != 'connection') return;
    error = null;
    errorSource = null;
  }

  void _onStreamError(Object failure) => _onConnection(false);

  void applyEvent(SessionModel event) {
    if (_disposed) return;
    if (event.instanceId != state.instanceId) {
      connected = false;
      awaitingReply = false;
      _fail('服务实例已变化，请重新连接并恢复快照。', 'connection');
      _notify();
      return;
    }
    if (event.sequence <= state.sequence) return;
    state = event;
    _settleReply(event);
    _notify();
  }

  /// 「等待老师回答」加载态的事件收口——文字与录音共用，纯事件驱动。
  ///
  /// - 老师回答（id 大于基线的非学生消息）到达 → 结束加载；
  /// - 录音链启动（transcribing）→ 与文字点击同效，立即进入加载；
  /// - 转写收口（failed/completed）→ 若无在途学生提问则不会再有回答，
  ///   微任务复查后收口（成功链的学生消息紧随 completed 到达，复查可保住）。
  void _settleReply(SessionModel model) {
    if (model.recording == 'transcribing') {
      // 录音转写即「提交」：立即进入加载，与文字发送的点击瞬时反馈一致。
      // 必须先臂载并抬高基线，否则旧的老师消息会被误判为「回答到达」。
      _replyFloorId = _maxMessageId();
      awaitingReply = true;
      return;
    }
    for (final ClassMessage message in model.messages) {
      if (message.role != 'student' && message.id > _replyFloorId) {
        awaitingReply = false;
        return;
      }
    }
    if (awaitingReply &&
        (model.recording == 'failed' || model.recording == 'completed')) {
      scheduleMicrotask(() {
        if (_disposed || !awaitingReply) return;
        // 成功链里学生消息在 completed 后同批到达：最后一条是学生消息
        // 说明提问已在途，继续等回答；否则（静音/识别失败）收口。
        final List<ClassMessage> messages = state.messages;
        if (messages.isNotEmpty && messages.last.role == 'student') return;
        awaitingReply = false;
        _notify();
      });
    }
  }

  int _maxMessageId() {
    int max = 0;
    for (final ClassMessage message in state.messages) {
      if (message.id > max) max = message.id;
    }
    return max;
  }

  /// [source] 标注失败命令归属的控件，供页面定位小错误框。
  Future<bool> _run(
    Future<void> Function() command, {
    required String source,
  }) async {
    if (_disposed || busy) return false;
    busy = true;
    error = null;
    errorSource = null;
    _notify();
    try {
      await command();
      // 连接级命令成功即证明服务可达（首屏快照、重连）。
      if (source == 'connection') {
        connected = true;
        _clearConnectionError();
      }
      return true;
    } on ClassroomFailure catch (failure) {
      if (source == 'connection') connected = false;
      _fail(failure.message, source);
      return false;
    } catch (_) {
      if (source == 'connection') connected = false;
      _fail('操作失败，请重试。', source);
      return false;
    } finally {
      busy = false;
      _notify();
    }
  }

  /// 同实例快照不得覆盖已经收到的更新事件。
  void _acceptSnapshot(SessionModel snapshot) {
    if (_disposed) return;
    if (snapshot.instanceId != state.instanceId ||
        snapshot.sequence >= state.sequence) {
      state = snapshot;
      _settleReply(snapshot);
    }
  }

  Future<bool> initialize() => _run(
    () async {
      _acceptSnapshot(await _service.snapshot());
    },
    source: 'connection',
  );

  /// 创建课堂：目标为空或超长时本地拦截，不发无效请求。
  Future<bool> createTask(String target) {
    final String trimmed = target.trim();
    if (trimmed.isEmpty || trimmed.length > 2000) {
      _fail('请输入 1–2000 字的课堂目标。', 'task');
      _notify();
      return Future<bool>.value(false);
    }
    return _run(() => _service.createTask(trimmed), source: 'task');
  }

  /// 摄像头 MJPEG 地址，供观察面板直接装载；数据源不支持时为空。
  Uri? cameraStreamUrl({int fps = 10}) => _service.cameraStreamUrl(fps: fps);

  /// 摄像头单帧地址，非 Web 平台轮询使用。
  Uri? cameraSnapshotUrl({int? maxWidth}) =>
      _service.cameraSnapshotUrl(maxWidth: maxWidth);
  /// 发送文字：点击瞬间进入加载态，直到老师回答事件到达或失败才结束。
  Future<bool> submitText(String text) async {
    if (_disposed || busy || awaitingReply) return false;
    // 校验先行：无效输入不进入加载态，箭头不会空转。
    if (text.trim().isEmpty || text.length > 4000) {
      _fail('请输入 1–4000 字的内容。', 'message');
      _notify();
      return false;
    }
    _replyFloorId = _maxMessageId();
    awaitingReply = true;
    _notify();
    final bool ok = await _run(
      () => _service.submitText(
        text,
        // 有待答问题时按回答提交并携带问题编号（合同 inputs 通道）。
        kind: state.pendingQuestionId != null ? 'answer' : 'question',
        questionId: state.pendingQuestionId,
      ),
      source: 'message',
    );
    if (!ok) {
      awaitingReply = false;
      _notify();
    }
    return ok;
  }
  Future<bool> record() => _run(
    () => state.recording == 'recording'
        ? _service.finishRecording()
        : _service.startRecording(
            kind: state.pendingQuestionId != null ? 'answer' : 'question',
            questionId: state.pendingQuestionId,
          ),
    source: 'record',
  );
  Future<bool> refresh() =>
      _run(() => _service.refreshObservation(), source: 'connection');

  /// 停止绕过普通命令的忙碌标记，失败时保留最后确认的任务状态。
  Future<void> stop() async {
    // 任务停止后回复不会再来，结束加载态。
    awaitingReply = false;
    try {
      await _service.stopTask();
      error = null;
      errorSource = null;
    } on ClassroomFailure catch (failure) {
      _fail(failure.message, 'task');
    } catch (_) {
      _fail('停止请求失败，尚未确认设备停止。请重试。', 'task');
    }
    _notify();
  }

  Future<bool> reconnect() => _run(
    () async {
      await _service.reconnect();
      _acceptSnapshot(await _service.snapshot());
      if (!_disposed) connected = true;
    },
    source: 'connection',
  );

  void selectScenario(DemoScenario value) {
    scenario = value;
    _service.setScenario(value);
    error = null;
    errorSource = null;
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
