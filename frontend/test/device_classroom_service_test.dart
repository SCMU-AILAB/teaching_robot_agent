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
String envelope(Object? data) =>
    jsonEncode(<String, Object?>{'code': 0, 'message': 'success', 'data': data});

/// SSE 事件帧（合同格式：id / event / data + 空行）。
String sseFrame(int sequence, String type, Map<String, dynamic> payload) {
  final String data = envelope(<String, Object?>{
    'instance_id': 'dev-1',
    'sequence': sequence,
    'task_id': 1,
    'occurred_at': sequence.toDouble(),
    'type': type,
    'payload': payload,
  });
  return 'id: dev-1:$sequence\nevent: $type\ndata: $data\n\n';
}

Map<String, dynamic> sessionBody() => <String, dynamic>{
  'instance_id': 'dev-1',
  'task': <String, dynamic>{
    'task_id': 1,
    'user_target': '认识桌面物体',
    'status': 'running',
    'action_ids': <int>[7],
    'created_at': 1.0,
    'ended_at': null,
  },
  'teaching': <String, dynamic>{
    'task_id': 1,
    'topic_id': 'topic-1',
    'goal': '认识物体',
    'stage': 'awaiting_answer',
    'pending_question': <String, dynamic>{
      'question_id': 'q-1',
      'text': '杯子是什么颜色？',
    },
    'last_feedback': null,
  },
  'actions': <Map<String, dynamic>>[
    <String, dynamic>{
      'action_id': 7,
      'raw_request': <String, dynamic>{
        'task_id': 1,
        'skill_name': 'scene_observe',
        'args': <String, dynamic>{},
        'timeout_s': 10.0,
      },
      'status': 'succeeded',
      'created_at': 1.0,
      'started_at': 1.0,
      'ended_at': 2.0,
      'result': null,
      'failure': null,
    },
  ],
  'latest_observation': <String, dynamic>{
    'observation_id': 'obs-1',
    'task_id': 1,
    'status': 'succeeded',
    'created_at': 1.0,
    'ended_at': 2.0,
    'snapshot': <String, dynamic>{
      'scene_id': 'scene-1',
      'task_id': 1,
      'question': '桌面有什么',
      'captured_at': 1.0,
      'analyzed_at': 2.0,
      'scene_revision': 3,
      'summary': '桌面上有一个绿色杯子。',
      'objects': <Object?>[],
      'relations': <Object?>[],
      'evidence_ids': <String>['ev-1'],
      'simulated': true,
      'model_id': 'local',
    },
    'from_cache': false,
    'stale': false,
    'error': null,
  },
  'recording': null,
  'playback': null,
  'assistant_messages': <Map<String, dynamic>>[
    <String, dynamic>{
      'message_id': 'm-1',
      'text': '你好，看看桌面有什么。',
      'created_at': 1.0,
      'speech_action_id': 8,
    },
  ],
  'truncated': <String, bool>{'actions': false, 'messages': false},
  'event_cursor': 'dev-1:5',
};

void main() {
  DeviceClassroomService build(MockClient client) =>
      DeviceClassroomService(baseUrl: 'http://device.test', client: client);

  test('统一响应错误包装为 ClassroomFailure', () async {
    final MockClient client = MockClient((http.Request request) async {
      return jsonResp(
        jsonEncode(<String, Object?>{
          'code': 40010,
          'message': '当前状态不允许操作',
          'data': null,
        }),
        409,
      );
    });
    final DeviceClassroomService service = build(client);
    addTearDown(service.dispose);
    await expectLater(
      service.snapshot(),
      throwsA(
        isA<ClassroomFailure>().having(
          (ClassroomFailure e) => e.message,
          'message',
          '当前状态不允许操作',
        ),
      ),
    );
  });

  test('播放失败原因进入设备说明，播报恢复后自动清掉', () async {
    int calls = 0;
    final MockClient client = MockClient((http.Request request) async {
      if (request.url.path == '/api/v1/system') {
        return jsonResp(
          envelope(<String, dynamic>{'instance_id': 'dev-1', 'active_task_id': 1}),
        );
      }
      if (request.url.path != '/api/v1/tasks/1/session') {
        return jsonResp(envelope(null));
      }
      calls++;
      final Map<String, dynamic> body = sessionBody();
      body['playback'] = calls == 1
          ? <String, dynamic>{
              'playback_id': 'p-1',
              'status': 'failed',
              'started_at': 1.0,
              'ended_at': 2.0,
              'error': <String, dynamic>{
                'code': 50003,
                'message': '扬声器被其他进程占用',
              },
            }
          : <String, dynamic>{
              'playback_id': 'p-2',
              'status': 'playing',
              'started_at': 3.0,
              'ended_at': null,
              'error': null,
            };
      return jsonResp(envelope(body));
    });
    final DeviceClassroomService service = build(client);
    addTearDown(service.dispose);
    expect((await service.snapshot()).note, '扬声器被其他进程占用');
    final SessionModel recovered = await service.snapshot();
    expect(recovered.playback, 'playing');
    expect(recovered.note, '');
  });

  test('createTask 按合同提交去重编号与目标', () async {
    http.Request? captured;
    final MockClient client = MockClient((http.Request request) async {
      if (request.method == 'POST' && request.url.path == '/api/v1/tasks') {
        captured = request;
        return jsonResp(
          envelope(<String, dynamic>{
            'task_id': 1,
            'user_target': '认识桌面物体',
            'status': 'pending',
            'action_ids': <int>[],
            'created_at': 1.0,
            'ended_at': null,
          }),
          202,
        );
      }
      return jsonResp(envelope(null), 200);
    });
    final DeviceClassroomService service = build(client);
    addTearDown(service.dispose);
    await service.createTask('认识桌面物体');
    final Map<String, dynamic> body =
        jsonDecode(captured!.body) as Map<String, dynamic>;
    expect(body['user_target'], '认识桌面物体');
    expect(body['client_request_id'], isA<String>());
    expect((body['client_request_id'] as String).length, lessThanOrEqualTo(128));
    expect(body.containsKey('topic_id'), isFalse);
  });

  test('聚合快照映射任务、阶段、待答问题、消息与动作', () async {
    final MockClient client = MockClient((http.Request request) async {
      if (request.url.path == '/api/v1/system') {
        return jsonResp(
          envelope(<String, dynamic>{'instance_id': 'dev-1', 'active_task_id': 1}),
          200,
        );
      }
      if (request.url.path == '/api/v1/tasks/1/session') {
        return jsonResp(envelope(sessionBody()), 200);
      }
      return jsonResp(envelope(null), 200);
    });
    final DeviceClassroomService service = build(client);
    addTearDown(service.dispose);
    final SessionModel state = await service.snapshot();
    expect(state.instanceId, 'dev-1');
    expect(state.taskId, 1);
    expect(state.target, '认识桌面物体');
    expect(state.taskStatus, 'running');
    expect(state.stage, 'awaiting_answer');
    expect(state.pendingQuestionId, 'q-1');
    expect(state.observed, isTrue);
    expect(state.observation, '桌面上有一个绿色杯子。');
    expect(state.messages, hasLength(1));
    expect(state.messages.single.role, 'assistant');
    expect(state.actions.single.label, 'scene_observe');
  });

  test('SSE 事件映射对话并按合同去重', () async {
    final String stream =
        sseFrame(6, 'assistant.message', <String, dynamic>{
          'message_id': 'm-2',
          'text': '杯子里有什么？',
          'created_at': 6.0,
          'speech_action_id': null,
        }) +
        sseFrame(7, 'input.updated', <String, dynamic>{
          'input_id': 'i-1',
          'task_id': 1,
          'kind': 'answer',
          'text': '有一个绿色杯子',
          'question_id': 'q-1',
          'source': 'text',
          'recording_id': null,
          'status': 'accepted',
          'created_at': 7.0,
          'error': null,
        }) +
        // 重复序列号：合同要求忽略已应用的事件。
        sseFrame(7, 'input.updated', <String, dynamic>{
          'input_id': 'i-2',
          'task_id': 1,
          'kind': 'answer',
          'text': '不应出现',
          'question_id': 'q-1',
          'source': 'text',
          'recording_id': null,
          'status': 'accepted',
          'created_at': 8.0,
          'error': null,
        });
    final MockClient client = MockClient((http.Request request) async {
      if (request.url.path == '/api/v1/system') {
        return jsonResp(
          envelope(<String, dynamic>{'instance_id': 'dev-1', 'active_task_id': 1}),
          200,
        );
      }
      if (request.url.path == '/api/v1/tasks/1/session') {
        return jsonResp(envelope(sessionBody()), 200);
      }
      if (request.url.path == '/api/v1/tasks/1/events') {
        // 断点续传头必须携带快照游标。
        expect(request.headers['Last-Event-ID'], 'dev-1:5');
        return jsonResp(stream, 200);
      }
      return jsonResp(envelope(null), 200);
    });
    final DeviceClassroomService service = build(client);
    addTearDown(service.dispose);
    final List<SessionModel> seen = <SessionModel>[];
    final StreamSubscription<SessionModel> sub =
        service.events.listen(seen.add);
    await service.snapshot();
    await Future<void>.delayed(const Duration(milliseconds: 50));
    await sub.cancel();
    expect(seen, isNotEmpty);
    final SessionModel state = seen.last;
    // 快照 1 条 + 新增老师/学生各 1 条；重复 input 被去重。
    expect(state.messages, hasLength(3));
    expect(state.messages[1].role, 'assistant');
    expect(state.messages[1].text, '杯子里有什么？');
    expect(state.messages[2].role, 'student');
    expect(state.messages[2].text, '有一个绿色杯子');
  });

  test('stream.reset 触发快照重取', () async {
    int sessionFetched = 0;
    final String stream = sseFrame(9, 'stream.reset', <String, dynamic>{
      'reason': 'instance_changed',
    });
    final MockClient client = MockClient((http.Request request) async {
      if (request.url.path == '/api/v1/system') {
        return jsonResp(
          envelope(<String, dynamic>{'instance_id': 'dev-1', 'active_task_id': 1}),
          200,
        );
      }
      if (request.url.path == '/api/v1/tasks/1/session') {
        sessionFetched += 1;
        return jsonResp(envelope(sessionBody()), 200);
      }
      if (request.url.path == '/api/v1/tasks/1/events') {
        return jsonResp(stream, 200);
      }
      return jsonResp(envelope(null), 200);
    });
    final DeviceClassroomService service = build(client);
    addTearDown(service.dispose);
    await service.snapshot();
    await Future<void>.delayed(const Duration(milliseconds: 50));
    expect(sessionFetched, greaterThanOrEqualTo(2));
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

  test('摄像头地址按合同携带帧率与最大宽度', () {
    final DeviceClassroomService service = build(
      MockClient((http.Request request) async => jsonResp(envelope(null))),
    );
    addTearDown(service.dispose);
    expect(
      service.cameraStreamUrl(fps: 8).toString(),
      'http://device.test/api/v1/camera/stream?fps=8',
    );
    expect(
      service.cameraSnapshotUrl(maxWidth: 960).toString(),
      'http://device.test/api/v1/camera/snapshot?max_width=960',
    );
  });

  test('submitText 携带 questionId 时按 answer 提交', () async {
    http.Request? captured;
    final MockClient client = MockClient((http.Request request) async {
      if (request.url.path == '/api/v1/system') {
        return jsonResp(
          envelope(<String, dynamic>{'instance_id': 'dev-1', 'active_task_id': 1}),
          200,
        );
      }
      if (request.url.path == '/api/v1/tasks/1/session') {
        return jsonResp(envelope(sessionBody()), 200);
      }
      if (request.url.path == '/api/v1/tasks/1/inputs') {
        captured = request;
        return jsonResp(envelope(<String, dynamic>{'input_id': 'i-9'}), 202);
      }
      return jsonResp(envelope(null), 200);
    });
    final DeviceClassroomService service = build(client);
    addTearDown(service.dispose);
    await service.snapshot();
    await service.submitText('绿色的', kind: 'answer', questionId: 'q-1');
    final Map<String, dynamic> body =
        jsonDecode(captured!.body) as Map<String, dynamic>;
    expect(body['kind'], 'answer');
    expect(body['question_id'], 'q-1');
    expect(body['text'], '绿色的');
    expect(body['client_request_id'], isA<String>());
  });
}
