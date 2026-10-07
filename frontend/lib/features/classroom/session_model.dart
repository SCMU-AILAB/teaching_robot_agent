// frontend/lib/features/classroom/session_model.dart

/// 页面专用状态快照，不声明为后端 HTTP 响应模型。
class SessionModel {
  const SessionModel({
    this.instanceId = 'local-demo',
    this.sequence = 0,
    this.taskId = 0,
    this.target = '',
    this.taskStatus = 'idle',
    this.stage = 'not_started',
    this.recording = 'idle',
    this.playback = 'idle',
    this.observation = '等待课堂开始后观察桌面。',
    this.observed = false,
    this.stale = false,
    this.messages = const [],
    this.actions = const [],
    this.recordingSeconds = 0,
    this.note = '',
  });

  final String instanceId;
  final int sequence;
  final int taskId;
  final String target;
  final String taskStatus;
  final String stage;
  final String recording;
  final String playback;
  final String observation;
  final bool observed;
  final bool stale;
  final List<ClassMessage> messages;
  final List<ClassAction> actions;
  final int recordingSeconds;
  final String note;

  bool get active => taskStatus == 'running' || taskStatus == 'cancelling';

  SessionModel copyWith({
    int? sequence,
    String? taskStatus,
    String? stage,
    String? recording,
    String? playback,
    String? observation,
    bool? observed,
    bool? stale,
    List<ClassMessage>? messages,
    List<ClassAction>? actions,
    int? recordingSeconds,
    String? note,
  }) => SessionModel(
    instanceId: instanceId,
    sequence: sequence ?? this.sequence,
    taskId: taskId,
    target: target,
    taskStatus: taskStatus ?? this.taskStatus,
    stage: stage ?? this.stage,
    recording: recording ?? this.recording,
    playback: playback ?? this.playback,
    observation: observation ?? this.observation,
    observed: observed ?? this.observed,
    stale: stale ?? this.stale,
    messages: List.unmodifiable(messages ?? this.messages),
    actions: List.unmodifiable(actions ?? this.actions),
    recordingSeconds: recordingSeconds ?? this.recordingSeconds,
    note: note ?? this.note,
  );
}

/// 对话消息使用独立编号，重复事件不会重复追加。
class ClassMessage {
  const ClassMessage(this.id, this.role, this.text);
  final int id;
  final String role;
  final String text;
}

/// 页面只展示模拟动作，不拥有真实动作执行能力。
class ClassAction {
  const ClassAction(this.id, this.label, this.status);
  final int id;
  final String label;
  final String status;
}

enum DemoScenario {
  normal,
  silence,
  asrFailure,
  synthesisFailure,
  requestFailure,
}

/// 用于模拟数据源与页面之间的明确失败。
class ClassroomFailure implements Exception {
  const ClassroomFailure(this.message);
  final String message;
  @override
  String toString() => message;
}
