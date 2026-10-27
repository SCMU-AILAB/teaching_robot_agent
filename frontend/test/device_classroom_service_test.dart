// frontend/test/device_classroom_service_test.dart
import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:teaching_robot_frontend/features/classroom/device_classroom_service.dart';
import 'package:teaching_robot_frontend/features/classroom/session_model.dart';

/// JSON 响应：显式 UTF-8，避免 http.Response 默认 Latin1 编码中文失败。
http.Response jsonResp(String body, [int status = 200]) => http.Response(
  body,
  status,
  headers: <String, String>{'content-type': 'application/json; charset=utf-8'},
);

/// 统一响应包装。
String envelope(Object? data) => jsonEncode(<String, Object?>{
  'code': 0,
  'message': 'success',
  'data': data,
});

/// SSE 事件帧（API_CONTRACT v1.0 第 4 节：id / event / data + 空行）。
String sseFrame(int sequence, String type, Map<String, dynamic> payload) {
  final String data = envelope(<String, Object?>{
    'instance_id': 'dev-1',
    'sequence': sequence,
    'task_id': 1,
    'occurred_at': sequence.toDouble(),
    'type': type,
    'payload': payload,
  });
  final String id = type == 'stream.reset' ? '' : 'id: dev-1:$sequence\n';
  return '${id}event: $type\ndata: $data\n\n';
}

Map<String, dynamic> message(
  String id,
  String role,
  String text, {
  String status = 'answered',
  Map<String, dynamic>? error,
}) => <String, dynamic>{
  'message_id': id,
  'task_id': 1,
  'role': role,
  'text': text,
  'source': role == 'user' ? 'text' : 'agent',
  'status': role == 'user' ? status : 'final',
  'reply_to': null,
  'speech_action_id': null,
  'error': error,
  'created_at': 1.0,
};

Map<String, dynamic> action(int id, String skill, String status) =>
    <String, dynamic>{
      'action_id': id,
      'skill_name': skill,
      'args': <String, dynamic>{},
      'status': status,
      'created_at': 1.0,
      'started_at': 1.0,
      'ended_at': null,
      'summary': null,
      'failure': null,
    };

Map<String, dynamic> recording(String status, {String? transcript}) =>
    <String, dynamic>{
      'recording_id': 'a' * 32,
      'task_id': 1,
      'status': status,
      'started_at': 1.0,
      'ended_at': null,
      'transcript': transcript,
      'message_id': null,
      'error': null,
    };

/// TaskSnapshotResponse。
Map<String, dynamic> snapshotBody({
  String taskStatus = 'running',
  List<Map<String, dynamic>>? messages,
  List<Map<String, dynamic>>? actions,
}) => <String, dynamic>{
  'instance_id': 'dev-1',
  'task': <String, dynamic>{
    'task_id': 1,
    'status': taskStatus,
    'user_target': '认识桌面物体',
    'created_at': 1.0,
    'ended_at': null,
  },
  'messages':
      messages ??
      <Map<String, dynamic>>[
        message('u-1', 'user', '桌上有什么？'),
        message('r-1', 'assistant', '有一个绿色杯子。'),
      ],
  'actions': actions ?? <Map<String, dynamic>>[action(7, 'speak', 'succeeded')],
  'latest_observation': <String, dynamic>{
    'observation_id': 'obs-1',
    'task_id': 1,
    'question': '桌上有什么',
    'summary': '桌面上有一个绿色杯子。',
    'objects': <Map<String, dynamic>>[
      <String, dynamic>{'label': '杯子', 'position': '左侧'},
    ],
    'captured_at': 1.0,
    'analyzed_at': 2.0,
    'stale': false,
    'simulated': false,
    'frame_evidence_id': null,
  },
  'active_recording': null,
  'truncated': <String, bool>{'messages': false, 'actions': false},
  'event_cursor': 'dev-1:5',
};

String systemBody({int? activeTaskId = 1}) => envelope(<String, dynamic>{
  'instance_id': 'dev-1',
  'active_task_id': activeTaskId,
  'camera': <String, dynamic>{'available': true},
});

/// 只响应系统与任务快照的设备；[extra] 处理其余请求。
MockClient device(
  Map<String, dynamic> Function() snapshot, {
  Future<http.Response> Function(http.Request request)? extra,
}) => MockClient((http.Request request) async {
  if (request.url.path == '/api/v1/system') return jsonResp(systemBody());
  if (request.method == 'GET' && request.url.path == '/api/v1/tasks/1') {
    return jsonResp(envelope(snapshot()));
  }
  if (extra != null) return extra(request);
  // 事件流默认空：立即结束。
  return jsonResp('');
});

void main() {
  DeviceClassroomService build(MockClient client) =>
      DeviceClassroomService(baseUrl: 'http://device.test', client: client);

  test('统一响应错误包装为 ClassroomFailure', () async {
    final MockClient client = MockClient(
      (http.Request request) async => jsonResp(
        jsonEncode(<String, Object?>{
          'code': 40010,
          'message': '上一条消息仍在处理',
          'data': null,
        }),
        409,
      ),
    );
    final DeviceClassroomService service = build(client);
    addTearDown(service.dispose);
    await expectLater(
      service.snapshot(),
      throwsA(
        isA<ClassroomFailure>().having(
          (ClassroomFailure e) => e.message,
          'message',
          '上一条消息仍在处理',
        ),
      ),
    );
  });

  test('任务快照映射任务、消息、动作与观察', () async {
    final DeviceClassroomService service = build(
      device(() => snapshotBody(taskStatus: 'pending')),
    );
    addTearDown(service.dispose);
    final SessionModel state = await service.snapshot();
    expect(state.instanceId, 'dev-1');
    expect(state.taskId, 1);
    expect(state.target, '认识桌面物体');
    // 合同的 pending 是尚未提交动作的进行中任务，页面按 running 交互。
    expect(state.taskStatus, 'running');
    expect(state.stage, 'awaiting_input');
    expect(state.pendingQuestionId, isNull);
    expect(state.observed, isTrue);
    expect(state.observation, '桌面上有一个绿色杯子。\n物体：杯子（左侧）');
    expect(state.messages.map((ClassMessage m) => m.role).toList(), <String>[
      'student',
      'assistant',
    ]);
    expect(state.actions.single.label, 'speak');
    expect(state.playback, 'completed');
  });

  test('播报状态取自最近的 speak 动作', () async {
    int calls = 0;
    final DeviceClassroomService service = build(
      device(() {
        calls += 1;
        return snapshotBody(
          actions: <Map<String, dynamic>>[
            action(7, 'speak', 'succeeded'),
            action(9, 'speak', calls == 1 ? 'failed' : 'running'),
          ],
        );
      }),
    );
    addTearDown(service.dispose);
    expect((await service.snapshot()).playback, 'failed');
    expect((await service.snapshot()).playback, 'playing');
  });

  test('createTask 只提交合同字段，读取快照游标后建流', () async {
    http.Request? created;
    String? cursor;
    final MockClient client = MockClient((http.Request request) async {
      if (request.method == 'POST' && request.url.path == '/api/v1/tasks') {
        created = request;
        return jsonResp(
          envelope(<String, dynamic>{
            'task_id': 1,
            'status': 'pending',
            'user_target': '认识桌面物体',
            'created_at': 1.0,
            'ended_at': null,
          }),
          201,
        );
      }
      if (request.url.path == '/api/v1/tasks/1') {
        return jsonResp(envelope(snapshotBody(messages: const [])));
      }
      if (request.url.path == '/api/v1/tasks/1/events') {
        cursor = request.headers['Last-Event-ID'];
      }
      return jsonResp('');
    });
    final DeviceClassroomService service = build(client);
    addTearDown(service.dispose);
    await service.createTask('认识桌面物体', topicId: 'ignored');
    final Map<String, dynamic> body =
        jsonDecode(created!.body) as Map<String, dynamic>;
    expect(body.keys.toSet(), <String>{'client_request_id', 'user_target'});
    expect(body['user_target'], '认识桌面物体');
    expect(
      (body['client_request_id'] as String).length,
      lessThanOrEqualTo(128),
    );
    expect(cursor, 'dev-1:5');
  });

  test('SSE 事件按序号去重，助手回答补全不重复追加', () async {
    final String stream =
        sseFrame(6, 'message.updated', message('u-2', 'user', '那插座呢？')) +
        sseFrame(7, 'message.updated', message('r-2', 'assistant', '插座')) +
        sseFrame(
          8,
          'message.updated',
          message('r-2', 'assistant', '插座里有电，不要用手碰。'),
        ) +
        // 重复序号：合同要求忽略已应用的事件。
        sseFrame(8, 'message.updated', message('r-3', 'assistant', '不应出现')) +
        sseFrame(9, 'action.updated', action(10, 'move_relative', 'running'));
    final DeviceClassroomService service = build(
      device(
        snapshotBody,
        extra: (http.Request request) async {
          expect(request.headers['Last-Event-ID'], 'dev-1:5');
          return jsonResp(stream);
        },
      ),
    );
    addTearDown(service.dispose);
    final List<SessionModel> seen = <SessionModel>[];
    final StreamSubscription<SessionModel> sub = service.events.listen(
      seen.add,
    );
    await service.snapshot();
    await Future<void>.delayed(const Duration(milliseconds: 50));
    await sub.cancel();
    final SessionModel state = seen.last;
    expect(state.messages.map((ClassMessage m) => m.text).toList(), <String>[
      '桌上有什么？',
      '有一个绿色杯子。',
      '那插座呢？',
      '插座里有电，不要用手碰。',
    ]);
    expect(state.actions.last.label, 'move_relative');
  });

  test('被拒绝的消息补一条机器人说明并写入设备说明', () async {
    final DeviceClassroomService service = build(
      device(
        () => snapshotBody(
          messages: <Map<String, dynamic>>[
            message(
              'u-1',
              'user',
              '你好',
              status: 'rejected',
              error: <String, dynamic>{
                'code': 50002,
                'message': '模型服务不可用，本条未执行，可以继续对话',
              },
            ),
          ],
        ),
      ),
    );
    addTearDown(service.dispose);
    final SessionModel state = await service.snapshot();
    expect(state.messages.last.role, 'assistant');
    expect(state.messages.last.text, contains('模型服务不可用'));
    expect(state.note, '模型服务不可用，本条未执行，可以继续对话');
    expect(state.stage, 'awaiting_input');
  });

  test('stream.reset 触发快照重取', () async {
    int fetched = 0;
    final DeviceClassroomService service = build(
      device(
        () {
          fetched += 1;
          return snapshotBody();
        },
        extra: (http.Request request) async => jsonResp(
          sseFrame(9, 'stream.reset', <String, dynamic>{
            'reason': 'cursor_expired',
          }),
        ),
      ),
    );
    addTearDown(service.dispose);
    await service.snapshot();
    await Future<void>.delayed(const Duration(milliseconds: 50));
    expect(fetched, greaterThanOrEqualTo(2));
  });

  test('网络不可达映射为明确的连接失败提示', () async {
    final DeviceClassroomService service = build(
      MockClient(
        (http.Request request) async =>
            throw http.ClientException('connection refused', request.url),
      ),
    );
    addTearDown(service.dispose);
    await expectLater(
      service.snapshot(),
      throwsA(
        isA<ClassroomFailure>().having(
          (ClassroomFailure e) => e.message,
          'message',
          contains('无法连接设备服务'),
        ),
      ),
    );
  });

  test('摄像头只有单帧快照地址，没有 MJPEG 流', () {
    final DeviceClassroomService service = build(
      MockClient((http.Request request) async => jsonResp(envelope(null))),
    );
    addTearDown(service.dispose);
    expect(service.cameraStreamUrl(fps: 8), isNull);
    expect(
      service.cameraSnapshotUrl(maxWidth: 960).toString(),
      'http://device.test/api/v1/camera/snapshot?max_width=960',
    );
  });

  test('submitText 只提交合同字段并立即显示用户消息', () async {
    http.Request? captured;
    final DeviceClassroomService service = build(
      device(
        () => snapshotBody(messages: const []),
        extra: (http.Request request) async {
          if (request.url.path != '/api/v1/tasks/1/messages') {
            return jsonResp('');
          }
          captured = request;
          return jsonResp(
            envelope(message('u-9', 'user', '绿色的', status: 'accepted')),
            202,
          );
        },
      ),
    );
    addTearDown(service.dispose);
    await service.snapshot();
    final Future<SessionModel> next = service.events.first;
    await service.submitText('绿色的', kind: 'answer', questionId: 'q-1');
    final Map<String, dynamic> body =
        jsonDecode(captured!.body) as Map<String, dynamic>;
    expect(body.keys.toSet(), <String>{'client_request_id', 'text'});
    expect(body['text'], '绿色的');
    final SessionModel state = await next;
    expect(state.messages.single.role, 'student');
    expect(state.stage, 'explaining');
  });

  test('录音开始与结束走合同的录音通道，空转写给出提示', () async {
    final List<String> calls = <String>[];
    final DeviceClassroomService service = build(
      device(
        snapshotBody,
        extra: (http.Request request) async {
          final String path = request.url.path;
          if (request.method == 'POST' &&
              path == '/api/v1/tasks/1/recordings') {
            calls.add('start ${request.body}');
            return jsonResp(envelope(recording('recording')), 201);
          }
          if (request.method == 'PATCH' &&
              path == '/api/v1/tasks/1/recordings/${'a' * 32}') {
            calls.add('finish ${request.body}');
            return jsonResp(
              envelope(recording('completed', transcript: '')),
              202,
            );
          }
          return jsonResp('');
        },
      ),
    );
    addTearDown(service.dispose);
    await service.snapshot();
    await service.startRecording(maxDurationS: 10);
    await service.finishRecording();
    expect(calls, hasLength(2));
    final Map<String, dynamic> start =
        jsonDecode(calls.first.substring(6)) as Map<String, dynamic>;
    expect(start.keys.toSet(), <String>{'client_request_id', 'max_duration_s'});
    expect(calls.last, 'finish {"status":"transcribing"}');
    final SessionModel state = await service.snapshot();
    expect(state.recording, 'idle');
  });
}
