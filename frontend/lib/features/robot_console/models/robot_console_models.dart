// frontend/lib/features/robot_console/models/robot_console_models.dart
//
// 原件因根目录 .gitignore 的 `models/` 规则未进入提交，此文件按控制器、模拟服务、
// 组件与测试中的用法重建；字段命名沿用 API_CONTRACT.md 的驼峰形式。

/// 演示场景，驱动模拟服务的分支行为。
enum ConsoleScenario {
  normal,
  silence,
  asrFailure,
  speechFailure,
  requestFailure,
  emptyObservation,
}

/// 页面可直接展示的失败说明。
class RobotConsoleFailure implements Exception {
  const RobotConsoleFailure(this.message);

  final String message;

  @override
  String toString() => message;
}

/// 对话消息；role 为 system、user 或 assistant。
class ChatMessage {
  const ChatMessage({
    required this.messageId,
    required this.role,
    required this.text,
    this.speechActionId,
  });

  final String messageId;
  final String role;
  final String text;

  /// assistant 回答对应的 speak 动作编号。
  final int? speechActionId;
}

/// 机器人动作及其执行状态。
class RobotAction {
  const RobotAction({
    required this.actionId,
    required this.skillName,
    required this.argsSummary,
    required this.status,
    this.resultSummary,
    this.failure,
  });

  final int actionId;
  final String skillName;
  final String argsSummary;
  final String status;
  final String? resultSummary;
  final String? failure;

  static const Set<String> _terminalStatuses = <String>{
    'succeeded',
    'failed',
    'cancelled',
    'timed_out',
  };

  /// 已进入终态，不会再变化。
  bool get terminal => _terminalStatuses.contains(status);

  RobotAction copyWith({
    String? status,
    String? resultSummary,
    String? failure,
  }) => RobotAction(
    actionId: actionId,
    skillName: skillName,
    argsSummary: argsSummary,
    status: status ?? this.status,
    resultSummary: resultSummary ?? this.resultSummary,
    failure: failure ?? this.failure,
  );
}

/// 观察中识别出的物体及其相对画面的方位。
class SceneObject {
  const SceneObject({
    required this.objectId,
    required this.label,
    required this.position,
  });

  final String objectId;
  final String label;

  /// 左侧、中间、右侧或不确定。
  final String position;
}

/// 与观察物体关联的安全资料条目。
class SafetyEntry {
  const SafetyEntry({
    required this.title,
    required this.explanation,
    required this.source,
  });

  final String title;
  final String explanation;
  final String source;
}

/// 一次场景观察；status 为 queued、running、succeeded 或 failed。
class SceneObservation {
  const SceneObservation({
    required this.observationId,
    required this.status,
    this.summary = '',
    this.objects = const <SceneObject>[],
    this.relatedSafety = const <SafetyEntry>[],
    this.capturedAt,
    this.modelId = '',
    this.simulated = true,
    this.stale = false,
    this.error,
  });

  final String observationId;
  final String status;
  final String summary;
  final List<SceneObject> objects;
  final List<SafetyEntry> relatedSafety;

  /// 采集时间，Unix 秒。
  final double? capturedAt;
  final String modelId;

  /// 默认视为模拟，只有真实感知结果才显式标记为 false。
  final bool simulated;

  /// 移动或场景变化后旧观察失效。
  final bool stale;
  final String? error;

  SceneObservation copyWith({String? status, bool? stale, String? error}) =>
      SceneObservation(
        observationId: observationId,
        status: status ?? this.status,
        summary: summary,
        objects: objects,
        relatedSafety: relatedSafety,
        capturedAt: capturedAt,
        modelId: modelId,
        simulated: simulated,
        stale: stale ?? this.stale,
        error: error ?? this.error,
      );
}

/// 设备与服务状态；perception、asr、tts 取 ready、unavailable、not_configured 等。
class RobotStatus {
  const RobotStatus({
    this.isMoving = false,
    this.poseText,
    this.modelId = 'simulated-vlm',
    this.perception = 'ready',
    this.asr = 'ready',
    this.tts = 'ready',
    this.blockedReason,
  });

  final bool isMoving;
  final String? poseText;
  final String modelId;
  final String perception;
  final String asr;
  final String tts;

  /// 执行层因停止无法确认而锁定的原因。
  final String? blockedReason;

  RobotStatus copyWith({
    bool? isMoving,
    String? poseText,
    String? blockedReason,
  }) => RobotStatus(
    isMoving: isMoving ?? this.isMoving,
    poseText: poseText ?? this.poseText,
    modelId: modelId,
    perception: perception,
    asr: asr,
    tts: tts,
    blockedReason: blockedReason ?? this.blockedReason,
  );
}

/// 控制台完整会话快照；事件同样携带完整快照，按 instanceId 与 sequence 去重。
class RobotSession {
  const RobotSession({
    this.instanceId = 'mock-instance',
    this.sequence = 0,
    this.taskId,
    this.taskTarget = '',
    this.taskStatus = 'idle',
    this.messages = const <ChatMessage>[],
    this.actions = const <RobotAction>[],
    this.observation,
    this.robot = const RobotStatus(),
    this.recordingStatus = 'idle',
    this.recordingSeconds = 0,
    this.maxRecordingSeconds = 15,
    this.playbackStatus = 'idle',
    this.processing = false,
    this.note = '',
  });

  final String instanceId;
  final int sequence;
  final int? taskId;
  final String taskTarget;

  /// idle、pending、running、cancelling、completed、failed 或 cancelled。
  final String taskStatus;
  final List<ChatMessage> messages;
  final List<RobotAction> actions;
  final SceneObservation? observation;
  final RobotStatus robot;
  final String recordingStatus;
  final int recordingSeconds;
  final int maxRecordingSeconds;
  final String playbackStatus;

  /// 当前有用户输入正在处理。
  final bool processing;

  /// 任务区展示的提示。
  final String note;

  /// 已创建过任务（含已结束的任务）。
  bool get hasTask => taskId != null;

  /// 任务仍接受输入。
  bool get taskActive => taskStatus == 'pending' || taskStatus == 'running';

  /// 录音、识别、合成或播放占用音频设备。
  bool get audioBusy =>
      recordingStatus == 'recording' ||
      recordingStatus == 'transcribing' ||
      playbackStatus == 'synthesizing' ||
      playbackStatus == 'playing';

  RobotSession copyWith({
    int? sequence,
    String? taskStatus,
    List<ChatMessage>? messages,
    List<RobotAction>? actions,
    SceneObservation? observation,
    RobotStatus? robot,
    String? recordingStatus,
    int? recordingSeconds,
    String? playbackStatus,
    bool? processing,
    String? note,
  }) => RobotSession(
    instanceId: instanceId,
    sequence: sequence ?? this.sequence,
    taskId: taskId,
    taskTarget: taskTarget,
    taskStatus: taskStatus ?? this.taskStatus,
    messages: messages ?? this.messages,
    actions: actions ?? this.actions,
    observation: observation ?? this.observation,
    robot: robot ?? this.robot,
    recordingStatus: recordingStatus ?? this.recordingStatus,
    recordingSeconds: recordingSeconds ?? this.recordingSeconds,
    maxRecordingSeconds: maxRecordingSeconds,
    playbackStatus: playbackStatus ?? this.playbackStatus,
    processing: processing ?? this.processing,
    note: note ?? this.note,
  );
}
