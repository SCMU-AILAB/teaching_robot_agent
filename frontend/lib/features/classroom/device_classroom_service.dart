// frontend/lib/features/classroom/device_classroom_service.dart
import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../core/constants/api_paths.dart';
import 'classroom_service.dart';
import 'session_model.dart';

/// 机器人后端真实数据源，按根目录 API_CONTRACT.md v1.0 通信。
///
/// 任务快照（GET /tasks/{id}）给出完整状态与事件游标，SSE 事件流
/// （GET /tasks/{id}/events，Last-Event-ID 断点续传）推送增量；命令走
/// 任务、消息、录音三个通道。v1.0 没有课堂阶段、观察请求与 MJPEG：
/// 观察由机器人在回答时发起，播报状态取自最近一个 speak 动作，摄像头
/// 画面轮询单帧快照。统一响应的非 0 code 包装为 [ClassroomFailure]。
/// 界面序列号在适配器内自增，合同 sequence 只用于事件去重。
class DeviceClassroomService implements ClassroomService {
  DeviceClassroomService({required String baseUrl, http.Client? client})
    : _client = client ?? http.Client(),
      _baseUrl = baseUrl.replaceAll(RegExp(r'/+$'), '');

  final http.Client _client;
  final String _baseUrl;

  static const Duration _requestTimeout = Duration(seconds: 10);
  static const Duration _streamRetryDelay = Duration(seconds: 3);

  /// 两次重置间隔短于此值视为连续重置，需要退避。
  static const Duration _resetWindow = Duration(seconds: 10);
  static const String _idleObservation = '机器人回答涉及现场物体时会先观察桌面。';

  final StreamController<SessionModel> _events = StreamController.broadcast();
  final StreamController<bool> _connections = StreamController.broadcast();

  /// 界面序列号：每次向页面发状态严格自增，配合控制器去重。
  int _emitSeq = 0;

  /// 合同事件序号：忽略已应用或更旧的事件。
  int _appliedSeq = -1;

  String _instanceId = 'device';
  int? _taskId;
  String? _cursor;
  String _target = '';
  String _taskStatus = 'idle';
  String? _recordingId;
  String _recording = 'idle';
  double? _recordingStartedAt;
  int _recordingSeconds = 0;
  String _observation = _idleObservation;
  bool _observed = false;
  bool _stale = false;
  String _note = '';

  /// 合同消息编号到界面消息编号；助手回答可能先以播报文本出现、随后补全。
  final Map<String, int> _messageIds = <String, int>{};
  final Map<int, ClassMessage> _messages = <int, ClassMessage>{};
  int _messageSeq = 0;

  /// 尚未处理完（accepted/processing）的用户消息。
  final Set<String> _pendingInputs = <String>{};
  final Map<int, Map<String, dynamic>> _actions = <int, Map<String, dynamic>>{};

  DateTime? _lastReset;
  StreamSubscription<String>? _sseSub;
  Timer? _retryTimer;
  Timer? _recordTimer;
  int _requestSeq = 0;
  bool _disposed = false;

  @override
  Stream<SessionModel> get events => _events.stream;

  @override
  Stream<bool> get connections => _connections.stream;

  // ========== 请求封装 ==========

  Uri _uri(String path, [Map<String, String>? query]) {
    final Uri base = Uri.parse('$_baseUrl$path');
    return query == null || query.isEmpty
        ? base
        : base.replace(queryParameters: query);
  }

  /// 每个创建请求带去重编号（合同 client_request_id，≤128 字符）。
  String _clientRequestId(String prefix) {
    _requestSeq += 1;
    return '${prefix}_${DateTime.now().millisecondsSinceEpoch}_$_requestSeq';
  }

  /// 解统一响应包装：非 0 code 抛 [ClassroomFailure]，返回 data 字段。
  dynamic _decode(http.Response response) {
    final Object? body;
    try {
      body = jsonDecode(utf8.decode(response.bodyBytes));
    } catch (_) {
      throw ClassroomFailure('设备响应无法解析（HTTP ${response.statusCode}）。');
    }
    if (body is! Map<String, dynamic>) {
      throw ClassroomFailure('设备响应格式不合法（HTTP ${response.statusCode}）。');
    }
    final int code = (body['code'] as num?)?.toInt() ?? -1;
    if (code != 0) {
      throw ClassroomFailure(
        body['message']?.toString() ?? '设备返回错误（code $code）',
      );
    }
    return body['data'];
  }

  Future<dynamic> _get(String path, {Map<String, String>? query}) =>
      _guard(_client.get(_uri(path, query)));

  Future<dynamic> _sendJson(
    String method,
    String path,
    Map<String, dynamic> body,
  ) {
    const Map<String, String> headers = <String, String>{
      'Content-Type': 'application/json; charset=utf-8',
    };
    final String encoded = jsonEncode(body);
    return _guard(switch (method) {
      'POST' => _client.post(_uri(path), headers: headers, body: encoded),
      'PATCH' => _client.patch(_uri(path), headers: headers, body: encoded),
      _ => throw ArgumentError('不支持的请求方法：$method'),
    });
  }

  Future<dynamic> _guard(Future<http.Response> call) async {
    try {
      return _decode(await call.timeout(_requestTimeout));
    } on TimeoutException {
      throw const ClassroomFailure('请求设备超时。');
    } on http.ClientException {
      // 网络不可达、连接被拒、跨源被拦都落到这里：明确指向地址或网络问题。
      throw const ClassroomFailure('无法连接设备服务，请确认设备在线且地址正确。');
    }
  }

  // ========== 快照 ==========

  @override
  Future<SessionModel> snapshot() async {
    await _reload();
    unawaited(_ensureStream());
    return _build();
  }

  @override
  Future<void> reconnect() async {
    _closeStream();
    await _reload();
    _emit();
    await _ensureStream();
  }

  /// 读取系统状态：有活动任务恢复它；否则保留刚结束的任务以显示其终态。
  Future<void> _reload() async {
    final dynamic system = await _get(ApiPaths.system);
    int? taskId;
    if (system is Map<String, dynamic>) {
      _instanceId = system['instance_id']?.toString() ?? _instanceId;
      final dynamic active = system['active_task_id'];
      if (active is num) taskId = active.toInt();
    }
    taskId ??= _taskId;
    if (taskId == null) {
      _resetTaskState();
      return;
    }
    try {
      await _loadTask(taskId);
    } on ClassroomFailure {
      // 旧任务记录已被后端清理或服务已重启：回到无任务状态。
      if (taskId == _taskId) {
        _resetTaskState();
        return;
      }
      rethrow;
    }
  }

  Future<void> _loadTask(int taskId) async {
    final dynamic data = await _get(ApiPaths.task(taskId));
    if (data is! Map<String, dynamic>) {
      throw const ClassroomFailure('任务快照格式不合法。');
    }
    _resetTaskState();
    _taskId = taskId;
    _instanceId = data['instance_id']?.toString() ?? _instanceId;
    _applyTask(data['task']);
    for (final dynamic item in _list(data['messages'])) {
      _applyMessage(item);
    }
    for (final dynamic item in _list(data['actions'])) {
      _applyAction(item);
    }
    _applyObservation(data['latest_observation']);
    _applyRecording(data['active_recording']);
    final String? cursor = data['event_cursor']?.toString();
    if (cursor != null) {
      _cursor = cursor;
      _appliedSeq = _seqOfCursor(cursor) ?? _appliedSeq;
    }
  }

  static List<dynamic> _list(dynamic value) =>
      value is List ? value : const <dynamic>[];

  /// 无任务：清空任务态，保留实例标识。
  void _resetTaskState() {
    _closeStream();
    _taskId = null;
    _cursor = null;
    _appliedSeq = -1;
    _target = '';
    _taskStatus = 'idle';
    _recordingId = null;
    _recording = 'idle';
    _recordingStartedAt = null;
    _stopRecordTick();
    _observation = _idleObservation;
    _observed = false;
    _stale = false;
    _note = '';
    _messageIds.clear();
    _messages.clear();
    _messageSeq = 0;
    _pendingInputs.clear();
    _actions.clear();
  }

  // ========== 资源映射 ==========

  void _applyTask(dynamic task) {
    if (task is! Map<String, dynamic>) return;
    _taskId = (task['task_id'] as num?)?.toInt() ?? _taskId;
    _target = task['user_target']?.toString() ?? _target;
    _taskStatus = task['status']?.toString() ?? _taskStatus;
  }

  /// MessageResponse：user 映射为学生消息，assistant 为机器人回答。
  ///
  /// 被拒绝的用户消息补一条机器人说明，页面据此结束“等待回答”。
  void _applyMessage(dynamic message) {
    if (message is! Map<String, dynamic>) return;
    final String? id = message['message_id']?.toString();
    if (id == null) return;
    final String role = message['role'] == 'user' ? 'student' : 'assistant';
    _putMessage(id, role, message['text']?.toString() ?? '');
    if (role != 'student') return;
    final String status = message['status']?.toString() ?? '';
    if (status == 'accepted' || status == 'processing') {
      _pendingInputs.add(id);
      return;
    }
    _pendingInputs.remove(id);
    if (status == 'rejected') {
      final dynamic error = message['error'];
      final String reason = error is Map<String, dynamic>
          ? error['message']?.toString() ?? '处理失败'
          : '处理失败';
      _note = reason;
      _putMessage('$id:rejected', 'assistant', '这句没有处理成：$reason');
    }
  }

  /// 按合同编号新增或更新消息，界面编号保持首次出现的顺序。
  void _putMessage(String key, String role, String text) {
    final int? existing = _messageIds[key];
    if (existing != null) {
      _messages[existing] = ClassMessage(existing, role, text);
      return;
    }
    _messageSeq += 1;
    _messageIds[key] = _messageSeq;
    _messages[_messageSeq] = ClassMessage(_messageSeq, role, text);
  }

  void _applyAction(dynamic action) {
    if (action is! Map<String, dynamic>) return;
    final int? id = (action['action_id'] as num?)?.toInt();
    if (id != null) _actions[id] = action;
  }

  /// 播报状态取自最近一个 speak 动作。
  String get _playback {
    final List<int> ids =
        _actions.keys
            .where((int id) => _actions[id]!['skill_name'] == 'speak')
            .toList()
          ..sort();
    if (ids.isEmpty) return 'idle';
    return switch (_actions[ids.last]!['status']?.toString()) {
      'queued' => 'synthesizing',
      'running' || 'verifying' => 'playing',
      'succeeded' => 'completed',
      'failed' || 'timed_out' => 'failed',
      'cancelling' || 'cancelled' => 'stopped',
      _ => 'idle',
    };
  }

  /// ObservationResponse：摘要加物体方位作为观察说明。
  void _applyObservation(dynamic observation) {
    if (observation is! Map<String, dynamic>) return;
    final String summary = observation['summary']?.toString() ?? '';
    final List<String> objects = <String>[
      for (final dynamic item in _list(observation['objects']))
        if (item is Map<String, dynamic>)
          '${item['label']}（${item['position']}）',
    ];
    _observation = <String>[
      if (summary.isNotEmpty) summary,
      if (objects.isNotEmpty) '物体：${objects.join('、')}',
    ].join('\n');
    if (_observation.isEmpty) _observation = '画面中没有识别出明确物体。';
    _observed = true;
    _stale = observation['stale'] == true;
  }

  /// RecordingResponse；没有进行中的录音时为 null。
  void _applyRecording(dynamic recording) {
    if (recording is! Map<String, dynamic>) {
      if (_recording == 'recording' || _recording == 'transcribing') {
        _recording = 'idle';
      }
      _recordingStartedAt = null;
      _stopRecordTick();
      return;
    }
    _recordingId = recording['recording_id']?.toString() ?? _recordingId;
    _recording = recording['status']?.toString() ?? _recording;
    _recordingStartedAt = (recording['started_at'] as num?)?.toDouble();
    switch (_recording) {
      case 'recording':
        // 新一轮录音开始即清空上一条说明，否则旧提示会一直挂着。
        _note = '';
        _startRecordTick();
        return;
      case 'failed':
        final dynamic error = recording['error'];
        _note = error is Map<String, dynamic>
            ? error['message']?.toString() ?? '录音或识别失败。'
            : '录音或识别失败。';
      case 'completed':
        if ((recording['transcript']?.toString() ?? '').trim().isEmpty) {
          _note = '没有识别到语音，请重新录音或输入文字。';
        }
    }
    _stopRecordTick();
  }

  /// 录音秒数按 started_at 本地推算，仅用于展示。
  void _startRecordTick() {
    _recordTimer ??= Timer.periodic(const Duration(seconds: 1), (_) {
      final double? startedAt = _recordingStartedAt;
      if (_disposed || startedAt == null) return;
      final double now = DateTime.now().millisecondsSinceEpoch / 1000.0;
      final int seconds = (now - startedAt).floor().clamp(0, 60);
      if (seconds == _recordingSeconds) return;
      _recordingSeconds = seconds;
      _emit();
    });
  }

  void _stopRecordTick() {
    _recordTimer?.cancel();
    _recordTimer = null;
    _recordingSeconds = 0;
  }

  /// v1.0 没有课堂阶段：处理中显示讲解中，任务进行中等待提问。
  String get _stage {
    if (_pendingInputs.isNotEmpty) return 'explaining';
    if (_taskStatus == 'running' || _taskStatus == 'pending') {
      return 'awaiting_input';
    }
    return 'not_started';
  }

  SessionModel _build() {
    _emitSeq += 1;
    final List<int> messageIds = _messages.keys.toList()..sort();
    final List<int> actionIds = _actions.keys.toList()..sort();
    return SessionModel(
      instanceId: _instanceId,
      sequence: _emitSeq,
      taskId: _taskId ?? 0,
      target: _target,
      // 页面按 running 判断可交互；合同的 pending 是尚未提交动作的进行中任务。
      taskStatus: _taskStatus == 'pending' ? 'running' : _taskStatus,
      stage: _stage,
      recording: _recording,
      playback: _playback,
      observation: _observation,
      observed: _observed,
      stale: _stale,
      messages: <ClassMessage>[for (final int id in messageIds) _messages[id]!],
      actions: <ClassAction>[
        for (final int id in actionIds)
          ClassAction(
            id,
            _actions[id]!['skill_name']?.toString() ?? '动作',
            _actions[id]!['status']?.toString() ?? '',
          ),
      ],
      recordingSeconds: _recordingSeconds,
      note: _note,
    );
  }

  void _emit() {
    if (_disposed || _events.isClosed) return;
    _events.add(_build());
  }

  // ========== SSE 事件流 ==========

  Future<void> _ensureStream() async {
    final int? taskId = _taskId;
    final String? cursor = _cursor;
    // 合同要求带游标建流；没有快照游标时先不建流。
    if (_disposed || _sseSub != null || taskId == null || cursor == null) {
      return;
    }
    try {
      final http.Request request = http.Request(
        'GET',
        _uri(ApiPaths.events(taskId)),
      )..headers['Last-Event-ID'] = cursor;
      final http.StreamedResponse response = await _client.send(request);
      if (response.statusCode != 200) {
        throw const ClassroomFailure('事件流建立失败。');
      }
      _connections.add(true);
      String eventType = '';
      String data = '';
      _sseSub = response.stream
          .transform(utf8.decoder)
          .transform(const LineSplitter())
          .listen(
            (String line) {
              if (line.isEmpty) {
                if (data.isNotEmpty) _onSseEvent(eventType, data);
                eventType = '';
                data = '';
                return;
              }
              if (line.startsWith(':')) return; // 心跳注释
              final int colon = line.indexOf(':');
              final String field = colon < 0 ? line : line.substring(0, colon);
              final String value = colon < 0
                  ? ''
                  : line.substring(colon + 1).replaceFirst(RegExp(r'^ '), '');
              switch (field) {
                case 'event':
                  eventType = value;
                case 'data':
                  data = data.isEmpty ? value : '$data\n$value';
                case 'id':
                  _cursor = value;
              }
            },
            onError: (Object _) => _onStreamBroken(),
            onDone: () {
              // 断流前冲刷未以空行收尾的最后一条事件。
              if (data.isNotEmpty) _onSseEvent(eventType, data);
              _onStreamBroken();
            },
            cancelOnError: true,
          );
    } catch (_) {
      _onStreamBroken();
    }
  }

  void _onStreamBroken() {
    _closeStream();
    if (_disposed || _taskId == null) return;
    _connections.add(false);
    _retryTimer?.cancel();
    _retryTimer = Timer(_streamRetryDelay, () => unawaited(_ensureStream()));
  }

  void _closeStream() {
    _retryTimer?.cancel();
    _retryTimer = null;
    unawaited(_sseSub?.cancel());
    _sseSub = null;
  }

  void _onSseEvent(String type, String data) {
    final Object? decoded;
    try {
      decoded = jsonDecode(data);
    } catch (_) {
      return;
    }
    if (decoded is! Map<String, dynamic>) return;
    final dynamic body = decoded['data'];
    if (body is! Map<String, dynamic>) return;
    final String eventType = body['type']?.toString() ?? type;
    if (eventType == 'stream.reset') {
      _onStreamReset();
      return;
    }
    final int sequence = (body['sequence'] as num?)?.toInt() ?? -1;
    if (sequence >= 0 && sequence <= _appliedSeq) return;
    if (sequence >= 0) _appliedSeq = sequence;
    final dynamic payload = body['payload'];
    switch (eventType) {
      case 'task.updated':
        _applyTask(payload);
      case 'message.updated':
        _applyMessage(payload);
      case 'action.updated':
        _applyAction(payload);
      case 'observation.created':
        _applyObservation(payload);
      case 'recording.updated':
        _applyRecording(payload);
      default:
        return;
    }
    _emit();
  }

  /// 订阅失效：重读快照后以新游标建流。
  ///
  /// 首次重置立即重连；短时间内连续重置（例如服务端持续判定游标无效）
  /// 按断线间隔退避，避免快照与建流在事件循环里空转。
  void _onStreamReset() {
    _closeStream();
    final DateTime now = DateTime.now();
    final DateTime? previous = _lastReset;
    _lastReset = now;
    final Duration delay =
        previous != null && now.difference(previous) < _resetWindow
        ? _streamRetryDelay
        : Duration.zero;
    unawaited(
      _reload()
          .then((_) {
            _emit();
            if (_disposed || _taskId == null) return;
            _retryTimer?.cancel();
            _retryTimer = Timer(delay, () => unawaited(_ensureStream()));
          })
          .catchError((Object _) {
            _onStreamBroken();
          }),
    );
  }

  /// 游标格式 `<instance_id>:<sequence>`，取末段数字。
  static int? _seqOfCursor(String cursor) {
    final int colon = cursor.lastIndexOf(':');
    if (colon < 0) return null;
    return int.tryParse(cursor.substring(colon + 1));
  }

  // ========== 命令通道 ==========

  /// [topicId] 为课堂演示保留，v1.0 不接受该字段，忽略。
  @override
  Future<void> createTask(String target, {String? topicId}) async {
    final dynamic data = await _sendJson(
      'POST',
      ApiPaths.tasks,
      <String, dynamic>{
        'client_request_id': _clientRequestId('task'),
        'user_target': target,
      },
    );
    final int? taskId = data is Map<String, dynamic>
        ? (data['task_id'] as num?)?.toInt()
        : null;
    if (taskId == null) throw const ClassroomFailure('创建任务的响应不合法。');
    // 建流需要快照游标；读取快照后再订阅，不会漏掉创建之后的事件。
    _closeStream();
    await _loadTask(taskId);
    _emit();
    await _ensureStream();
  }

  /// v1.0 只有文字消息；[kind] 与 [questionId] 为课堂演示保留，忽略。
  @override
  Future<void> submitText(
    String text, {
    String kind = 'question',
    String? questionId,
  }) async {
    final int taskId = _requireTask();
    // 清掉上一条设备说明，避免旧失败提示挂在新一轮提问旁边。
    _note = '';
    final dynamic data = await _sendJson(
      'POST',
      ApiPaths.messages(taskId),
      <String, dynamic>{
        'client_request_id': _clientRequestId('message'),
        'text': text,
      },
    );
    _applyMessage(data);
    _emit();
  }

  @override
  Future<void> startRecording({
    String kind = 'question',
    String? questionId,
    double maxDurationS = 15,
  }) async {
    final int taskId = _requireTask();
    _note = '';
    final dynamic data =
        await _sendJson('POST', ApiPaths.recordings(taskId), <String, dynamic>{
          'client_request_id': _clientRequestId('recording'),
          'max_duration_s': maxDurationS,
        });
    _applyRecording(data);
    _emit();
  }

  @override
  Future<void> finishRecording() async {
    final int taskId = _requireTask();
    final String? recordingId = _recordingId;
    if (recordingId == null || _recording != 'recording') {
      throw const ClassroomFailure('没有进行中的录音。');
    }
    final dynamic data = await _sendJson(
      'PATCH',
      ApiPaths.recording(taskId, recordingId),
      <String, dynamic>{'status': 'transcribing'},
    );
    _applyRecording(data);
    _emit();
  }

  @override
  Future<void> stopTask() async {
    final int taskId = _requireTask();
    final dynamic data = await _sendJson(
      'PATCH',
      ApiPaths.task(taskId),
      <String, dynamic>{'status': 'cancelling'},
    );
    _applyTask(data);
    _emit();
  }

  /// v1.0 的观察由机器人发起：以一句提问请机器人重新看一看。
  @override
  Future<void> refreshObservation({String question = '请看看现在桌上有什么。'}) =>
      submitText(question);

  int _requireTask() {
    final int? taskId = _taskId;
    if (taskId == null ||
        (_taskStatus != 'running' && _taskStatus != 'pending')) {
      throw const ClassroomFailure('请先开始任务。');
    }
    return taskId;
  }

  @override
  void setScenario(DemoScenario scenario) {
    // 真实数据源没有演示场景；保持空实现以满足页面统一接口。
  }

  @override
  void disconnect() {
    _closeStream();
    _stopRecordTick();
    if (!_disposed && !_connections.isClosed) _connections.add(false);
  }

  @override
  Uri? cameraSnapshotUrl({int? maxWidth}) => _uri(
    ApiPaths.cameraSnapshot,
    maxWidth != null ? <String, String>{'max_width': '$maxWidth'} : null,
  );

  /// v1.0 没有 MJPEG 流，画面由页面轮询单帧快照。
  @override
  Uri? cameraStreamUrl({int fps = 10}) => null;

  @override
  void dispose() {
    _disposed = true;
    disconnect();
    unawaited(_events.close());
    unawaited(_connections.close());
    _client.close();
  }
}
