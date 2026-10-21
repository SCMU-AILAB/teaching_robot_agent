// frontend/lib/features/classroom/device_classroom_service.dart
import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'classroom_service.dart';
import 'session_model.dart';

/// Jetson 设备端真实数据源：按 API_CONTRACT.md 与 /api/v1 通信。
///
/// 覆盖合同的聚合快照（GET /tasks/{id}/session）、SSE 事件流
/// （GET /tasks/{id}/events，Last-Event-ID 断点续传）、任务/输入/录音/
/// 观察/取消通道。服务地址由配置注入，不写死；统一响应错误包装为
/// [ClassroomFailure]。界面序列号在适配器内自增，合同 sequence 仅用于
/// SSE 重放去重（忽略已应用或更旧的事件）。
class DeviceClassroomService implements ClassroomService {
  DeviceClassroomService({required String baseUrl, http.Client? client})
    : _client = client ?? http.Client(),
      _baseUrl = baseUrl.replaceAll(RegExp(r'/+$'), '');

  final http.Client _client;
  final String _baseUrl;

  static const Duration _requestTimeout = Duration(seconds: 10);

  /// 语音链路专用超时：服务器识别约 6 s、生成与合成再叠加几秒，
  /// 板端首次调用还要加载模型，10 s 会假超时。
  static const Duration _speechTimeout = Duration(seconds: 30);
  static const Duration _streamRetryDelay = Duration(seconds: 3);

  final StreamController<SessionModel> _events = StreamController.broadcast();
  final StreamController<bool> _connections = StreamController.broadcast();

  /// 界面序列号：每次向页面发状态严格自增，配合控制器去重。
  int _emitSeq = 0;

  /// 合同事件序列号：SSE 重放去重，忽略已应用或更旧的事件。
  int _appliedSeq = -1;

  String _instanceId = 'device';
  int? _taskId;
  String? _recordingId;
  String? _cursor;
  String _target = '';
  String _taskStatus = 'idle';
  String _stage = 'not_started';
  String? _pendingQuestionId;
  String _recording = 'idle';
  String _playback = 'idle';
  String _observation = '等待课堂开始后观察桌面。';
  bool _observed = false;
  bool _stale = false;
  String _note = '';
  double? _recordingStartedAt;
  int _recordingSeconds = 0;
  int _messageSeq = 0;
  final Map<String, int> _messageIds = <String, int>{};
  List<ClassMessage> _messages = const [];
  final Map<int, ClassAction> _actions = <int, ClassAction>{};

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
    final String suffix = query == null || query.isEmpty
        ? ''
        : '?${query.entries.map(
            (MapEntry<String, String> e) =>
                '${Uri.encodeQueryComponent(e.key)}='
                '${Uri.encodeQueryComponent(e.value)}',
          ).join('&')}';
    return Uri.parse('$_baseUrl/api/v1/$path$suffix');
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
      final String message =
          body['message']?.toString() ?? '设备返回错误（code $code）';
      throw ClassroomFailure(message);
    }
    return body['data'];
  }

  Future<dynamic> _get(String path, {Map<String, String>? query}) async {
    try {
      final http.Response response = await _client
          .get(_uri(path, query))
          .timeout(_requestTimeout);
      return _decode(response);
    } on TimeoutException {
      throw const ClassroomFailure('请求设备超时。');
    } on http.ClientException {
      // 网络不可达、连接被拒、跨源被拦都落到这里：明确指向地址或网络问题。
      throw const ClassroomFailure('无法连接设备服务，请确认设备在线且地址正确。');
    }
  }

  Future<dynamic> _sendJson(
    String method,
    String path,
    Map<String, dynamic> body, {
    Duration? timeout,
  }) async {
    try {
      final Future<http.Response> call = switch (method) {
        'POST' => _client.post(
          _uri(path),
          headers: const <String, String>{
            'Content-Type': 'application/json; charset=utf-8',
          },
          body: jsonEncode(body),
        ),
        'PATCH' => _client.patch(
          _uri(path),
          headers: const <String, String>{
            'Content-Type': 'application/json; charset=utf-8',
          },
          body: jsonEncode(body),
        ),
        _ => throw ArgumentError('不支持的请求方法：$method'),
      };
      return _decode(await call.timeout(timeout ?? _requestTimeout));
    } on TimeoutException {
      throw const ClassroomFailure('请求设备超时。');
    } on http.ClientException {
      // 网络不可达、连接被拒、跨源被拦都落到这里：明确指向地址或网络问题。
      throw const ClassroomFailure('无法连接设备服务，请确认设备在线且地址正确。');
    }
  }

  // ========== 快照与事件 ==========

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

  /// 拉 system + 聚合快照，整体替换任务态镜像（合同：快照与游标原子生成）。
  Future<void> _reload() async {
    final dynamic system = await _get('system');
    int? taskId;
    if (system is Map<String, dynamic>) {
      _instanceId = system['instance_id']?.toString() ?? _instanceId;
      final dynamic active = system['active_task_id'];
      if (active is num) taskId = active.toInt();
    }
    if (taskId == null) {
      _resetTaskState();
      return;
    }
    _taskId = taskId;
    final dynamic session = await _get('tasks/$taskId/session');
    if (session is Map<String, dynamic>) _applySession(session);
  }

  /// 无活动任务：清空任务态，保留实例标识。
  void _resetTaskState() {
    _taskId = null;
    _recordingId = null;
    _cursor = null;
    _appliedSeq = -1;
    _target = '';
    _taskStatus = 'idle';
    _stage = 'not_started';
    _pendingQuestionId = null;
    _recording = 'idle';
    _playback = 'idle';
    _observation = '等待课堂开始后观察桌面。';
    _observed = false;
    _stale = false;
    _note = '';
    _messages = const [];
    _messageIds.clear();
    _actions.clear();
    _stopRecordTick();
    _recordingStartedAt = null;
    _closeStream();
  }

  void _applySession(Map<String, dynamic> session) {
    _instanceId = session['instance_id']?.toString() ?? _instanceId;
    final dynamic task = session['task'];
    if (task is Map<String, dynamic>) {
      _taskId = (task['task_id'] as num?)?.toInt() ?? _taskId;
      _target = task['user_target']?.toString() ?? _target;
      _taskStatus = task['status']?.toString() ?? _taskStatus;
    }
    final dynamic teaching = session['teaching'];
    if (teaching is Map<String, dynamic>) {
      _stage = teaching['stage']?.toString() ?? _stage;
      final dynamic pending = teaching['pending_question'];
      _pendingQuestionId = pending is Map<String, dynamic>
          ? pending['question_id']?.toString()
          : null;
    }

    _actions.clear();
    final List<dynamic> actions = session['actions'] is List
        ? session['actions'] as List<dynamic>
        : const <dynamic>[];
    for (final dynamic item in actions) {
      if (item is Map<String, dynamic>) {
        final int? id = (item['action_id'] as num?)?.toInt();
        if (id != null) _actions[id] = _actionFrom(item);
      }
    }

    _applyObservation(session['latest_observation']);
    _applyRecording(session['recording']);
    final dynamic playback = session['playback'];
    if (playback is Map<String, dynamic>) {
      _playback = playback['status']?.toString() ?? _playback;
      _applyPlaybackNote(_playback, playback['error']);
    }

    _rebuildMessages(session);
    final String? cursor = session['event_cursor']?.toString();
    if (cursor != null) {
      _cursor = cursor;
      _appliedSeq = _seqOfCursor(cursor) ?? _appliedSeq;
    }
  }

  /// 消息列表按 created_at 归并重建。
  ///
  /// 合同 v0.2 的 SessionSnapshotResponse 只含 assistant_messages；
  /// 学生输入若在快照中出现（inputs，后续合同扩展）则一并归并。
  void _rebuildMessages(Map<String, dynamic> session) {
    final List<dynamic> assistant = session['assistant_messages'] is List
        ? session['assistant_messages'] as List<dynamic>
        : const <dynamic>[];
    final List<dynamic> inputs = session['inputs'] is List
        ? session['inputs'] as List<dynamic>
        : const <dynamic>[];
    final List<Map<String, dynamic>> items = <Map<String, dynamic>>[
      for (final dynamic item in assistant)
        if (item is Map<String, dynamic>) item,
      for (final dynamic item in inputs)
        if (item is Map<String, dynamic>) item,
    ]..sort(
      (Map<String, dynamic> a, Map<String, dynamic> b) =>
          ((a['created_at'] as num?) ?? 0).toDouble().compareTo(
            ((b['created_at'] as num?) ?? 0).toDouble(),
          ),
    );
    _messageIds.clear();
    _messages = const [];
    _messageSeq = 0;
    for (final Map<String, dynamic> item in items) {
      final String? key = (item['message_id'] ?? item['input_id'])?.toString();
      if (key == null) continue;
      final String role = item.containsKey('message_id')
          ? 'assistant'
          : 'student';
      _appendMessage(key, role, item['text']?.toString() ?? '');
    }
  }

  ClassAction _actionFrom(Map<String, dynamic> item) {
    final dynamic raw = item['raw_request'];
    final String label = raw is Map<String, dynamic>
        ? raw['skill_name']?.toString() ?? '动作'
        : '动作';
    return ClassAction(
      (item['action_id'] as num?)?.toInt() ?? 0,
      label,
      item['status']?.toString() ?? '',
    );
  }

  void _applyObservation(dynamic observation) {
    if (observation is! Map<String, dynamic>) return;
    _observed = observation['status']?.toString() == 'succeeded';
    _stale = observation['stale'] == true;
    final dynamic snapshot = observation['snapshot'];
    if (snapshot is Map<String, dynamic>) {
      final String? summary = snapshot['summary']?.toString();
      if (summary != null && summary.isNotEmpty) _observation = summary;
    }
    final dynamic error = observation['error'];
    if (error is Map<String, dynamic> && error['message'] != null) {
      _note = error['message'].toString();
    }
  }

  /// 把设备端 error 归一成一句可展示的话。
  ///
  /// 合同里 `playback.error` 的类型尚未定稿（string | object 待审核），
  /// 两种形态都接，避免因为字段形态变化把失败原因丢掉。
  void _noteFromError(dynamic error) {
    if (error is String && error.isNotEmpty) {
      _note = error;
      return;
    }
    if (error is Map<String, dynamic> && error['message'] != null) {
      _note = error['message'].toString();
    }
  }

  /// 播放阶段决定是否更新设备说明：开始播放说明上一轮失败已翻篇，
  /// failed/stopped 才需要把原因留在页面上。
  void _applyPlaybackNote(String status, dynamic error) {
    if (status == 'playing' || status == 'completed') {
      _note = '';
      return;
    }
    if (status == 'failed' || status == 'stopped') _noteFromError(error);
  }

  void _applyRecording(dynamic recording) {
    if (recording is! Map<String, dynamic>) {
      _recording = 'idle';
      _recordingStartedAt = null;
      _stopRecordTick();
      return;
    }
    _recordingId = recording['recording_id']?.toString() ?? _recordingId;
    _recording = recording['status']?.toString() ?? _recording;
    // 新一轮录音开始即清空上一条失败说明，否则旧提示会一直挂着。
    if (_recording == 'recording') _note = '';
    _recordingStartedAt = (recording['started_at'] as num?)?.toDouble();
    _noteFromError(recording['error']);
    if (_recording == 'recording') {
      _startRecordTick();
    } else {
      _stopRecordTick();
    }
  }

  /// 录音秒数按 started_at 本地推算（仅展示，不回写服务端）。
  void _startRecordTick() {
    _recordTimer ??= Timer.periodic(const Duration(seconds: 1), (_) {
      final double? startedAt = _recordingStartedAt;
      if (_disposed || startedAt == null) return;
      final int seconds = (_nowSeconds() - startedAt).floor().clamp(0, 60);
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

  double _nowSeconds() => DateTime.now().millisecondsSinceEpoch / 1000.0;

  void _appendMessage(String key, String role, String text) {
    if (_messageIds.containsKey(key)) return;
    _messageSeq += 1;
    _messageIds[key] = _messageSeq;
    _messages = <ClassMessage>[
      ..._messages,
      ClassMessage(_messageSeq, role, text),
    ];
  }

  SessionModel _build() {
    _emitSeq += 1;
    final List<int> actionIds = _actions.keys.toList()..sort();
    return SessionModel(
      instanceId: _instanceId,
      sequence: _emitSeq,
      taskId: _taskId ?? 0,
      target: _target,
      taskStatus: _taskStatus,
      stage: _stage,
      recording: _recording,
      playback: _playback,
      observation: _observation,
      observed: _observed,
      stale: _stale,
      messages: _messages,
      actions: <ClassAction>[for (final int id in actionIds) _actions[id]!],
      recordingSeconds: _recordingSeconds,
      note: _note,
      pendingQuestionId: _pendingQuestionId,
    );
  }

  void _emit() {
    if (_disposed || _events.isClosed) return;
    _events.add(_build());
  }

  // ========== SSE 事件流 ==========

  Future<void> _ensureStream() async {
    if (_disposed || _sseSub != null || _taskId == null) return;
    final int taskId = _taskId!;
    try {
      final http.Request request = http.Request(
        'GET',
        _uri('tasks/$taskId/events'),
      );
      if (_cursor != null) request.headers['Last-Event-ID'] = _cursor!;
      final http.StreamedResponse response = await _client.send(request);
      if (response.statusCode != 200) {
        throw ClassroomFailure('事件流建立失败。');
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
                  : line
                        .substring(colon + 1)
                        .replaceFirst(RegExp(r'^ '), '');
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
    final dynamic rawBody = decoded['data'];
    if (rawBody is! Map<String, dynamic>) return;
    final int sequence = (rawBody['sequence'] as num?)?.toInt() ?? -1;
    // 合同：忽略已应用或更旧的事件。
    if (sequence >= 0 && sequence <= _appliedSeq) return;
    if (sequence >= 0) _appliedSeq = sequence;
    final String? instance = rawBody['instance_id']?.toString();
    if (instance != null) _instanceId = instance;
    final String eventType = rawBody['type']?.toString() ?? type;
    final dynamic rawPayload = rawBody['payload'];
    final Map<String, dynamic>? payload = rawPayload is Map<String, dynamic>
        ? rawPayload
        : null;

    switch (eventType) {
      case 'task.updated':
        if (payload != null) {
          _target = payload['user_target']?.toString() ?? _target;
          _taskStatus = payload['status']?.toString() ?? _taskStatus;
          _taskId = (payload['task_id'] as num?)?.toInt() ?? _taskId;
        }
      case 'teaching.updated':
        if (payload != null) {
          _stage = payload['stage']?.toString() ?? _stage;
          final dynamic pending = payload['pending_question'];
          _pendingQuestionId = pending is Map<String, dynamic>
              ? pending['question_id']?.toString()
              : null;
        }
      case 'action.updated':
        if (payload != null) {
          final int? id = (payload['action_id'] as num?)?.toInt();
          if (id != null) _actions[id] = _actionFrom(payload);
        }
      case 'observation.updated':
        _applyObservation(payload);
      case 'recording.updated':
        _applyRecording(payload);
      case 'input.updated':
        if (payload != null && payload['input_id'] != null) {
          _appendMessage(
            payload['input_id'].toString(),
            'student',
            payload['text']?.toString() ?? '',
          );
        }
      case 'playback.updated':
        _playback = payload?['status']?.toString() ?? _playback;
        _applyPlaybackNote(_playback, payload?['error']);
      case 'camera.status':
        final dynamic error = payload?['error'];
        if (error is Map<String, dynamic> && error['message'] != null) {
          _note = error['message'].toString();
        }
      case 'assistant.message':
        if (payload != null && payload['message_id'] != null) {
          _appendMessage(
            payload['message_id'].toString(),
            'assistant',
            payload['text']?.toString() ?? '',
          );
        }
      case 'stream.reset':
        _onStreamBroken();
        unawaited(_reload().then((_) => _emit()).catchError((Object _) {}));
        return;
    }
    _emit();
  }

  /// 游标格式 `<instance_id>:<sequence>`，取末段数字。
  static int? _seqOfCursor(String cursor) {
    final int colon = cursor.lastIndexOf(':');
    if (colon < 0) return null;
    return int.tryParse(cursor.substring(colon + 1));
  }

  // ========== 命令通道 ==========

  @override
  Future<void> createTask(String target, {String? topicId}) async {
    final dynamic data = await _sendJson('POST', 'tasks', <String, dynamic>{
      'client_request_id': _clientRequestId('task'),
      'user_target': target,
      'topic_id': ?topicId,
    });
    if (data is Map<String, dynamic>) {
      _taskId = (data['task_id'] as num?)?.toInt() ?? _taskId;
      _target = data['user_target']?.toString() ?? target;
      _taskStatus = data['status']?.toString() ?? 'running';
      _stage = 'not_started';
    }
    _emit();
    await _ensureStream();
  }

  @override
  Future<void> submitText(
    String text, {
    String kind = 'question',
    String? questionId,
  }) async {
    final int taskId = _requireTask();
    // 清掉上一条设备说明，避免旧失败提示挂在新一轮提问旁边。
    _note = '';
    await _sendJson(
      'POST',
      'tasks/$taskId/inputs',
      <String, dynamic>{
        'client_request_id': _clientRequestId('input'),
        'kind': kind,
        'text': text,
        'question_id': questionId,
      },
      timeout: _speechTimeout,
    );
  }

  @override
  Future<void> startRecording({
    String kind = 'question',
    String? questionId,
    double maxDurationS = 15,
  }) async {
    final int taskId = _requireTask();
    _note = '';
    final dynamic data = await _sendJson(
      'POST',
      'tasks/$taskId/recordings',
      <String, dynamic>{
        'client_request_id': _clientRequestId('rec'),
        'kind': kind,
        'question_id': questionId,
        'max_duration_s': maxDurationS,
      },
    );
    if (data is Map<String, dynamic>) {
      _recordingId = data['recording_id']?.toString();
      _applyRecording(data);
    }
    _emit();
  }

  @override
  Future<void> finishRecording() async {
    final int taskId = _requireTask();
    final String? recordingId = _recordingId;
    if (recordingId == null) {
      throw ClassroomFailure('没有进行中的录音。');
    }
    final dynamic data = await _sendJson(
      'PATCH',
      'tasks/$taskId/recordings/$recordingId',
      <String, dynamic>{'status': 'transcribing'},
      timeout: _speechTimeout,
    );
    _applyRecording(data);
    _emit();
  }

  @override
  Future<void> stopTask() async {
    final int taskId = _requireTask();
    final dynamic data = await _sendJson(
      'PATCH',
      'tasks/$taskId',
      <String, dynamic>{'status': 'cancelling'},
    );
    if (data is Map<String, dynamic>) {
      _taskStatus = data['status']?.toString() ?? _taskStatus;
    }
    _emit();
  }

  @override
  Future<void> refreshObservation({
    String question = '请描述画面中的物体及其相对位置。',
  }) async {
    final int taskId = _requireTask();
    await _sendJson('POST', 'tasks/$taskId/observations', <String, dynamic>{
      'client_request_id': _clientRequestId('obs'),
      'question': question,
    });
  }

  int _requireTask() {
    final int? taskId = _taskId;
    if (taskId == null) throw ClassroomFailure('尚未创建教学任务。');
    return taskId;
  }

  @override
  void setScenario(DemoScenario scenario) {
    // 真实数据源无演示场景；保持空实现以满足页面统一接口。
  }

  @override
  void disconnect() {
    _closeStream();
    _stopRecordTick();
    if (!_disposed && !_connections.isClosed) _connections.add(false);
  }

  @override
  Uri? cameraSnapshotUrl({int? maxWidth}) => _uri(
    'camera/snapshot',
    maxWidth != null ? <String, String>{'max_width': '$maxWidth'} : null,
  );

  @override
  Uri? cameraStreamUrl({int fps = 10}) =>
      _uri('camera/stream', <String, String>{'fps': '$fps'});

  @override
  void dispose() {
    _disposed = true;
    disconnect();
    unawaited(_events.close());
    unawaited(_connections.close());
    _client.close();
  }
}
