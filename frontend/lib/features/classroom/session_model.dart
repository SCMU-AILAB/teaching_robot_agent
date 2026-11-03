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
    this.observation = '开始陪伴后，机器人会看看周围。',
    this.observed = false,
    this.stale = false,
    this.messages = const [],
    this.actions = const [],
    this.recordingSeconds = 0,
    this.note = '',
    this.pendingQuestionId,
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

  /// 当前待答问题编号（合同 TeachingStateResponse.pending_question）。
  ///
  /// 非空时学生输入按 answer 提交并携带此编号；null 表示自由提问。
  final String? pendingQuestionId;

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
    String? pendingQuestionId,
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
    pendingQuestionId: pendingQuestionId ?? this.pendingQuestionId,
  );
}

/// 对话消息使用独立编号，重复事件不会重复追加。
///
/// [role] 为 student（孩子）或 assistant（机器人）；[proactive] 表示机器人
/// 主动说的话（合同 source=proactive：主动提醒、探索讲解与总结）。
class ClassMessage {
  const ClassMessage(this.id, this.role, this.text, {this.proactive = false});
  final int id;
  final String role;
  final String text;
  final bool proactive;
}

/// 机器人动作的只读展示：label 为可读描述，status 为合同动作状态。
class ClassAction {
  const ClassAction(this.id, this.label, this.status, {this.motion = false});
  final int id;
  final String label;
  final String status;

  /// 是否为车体运动（前进、后退、转向、导航）。
  final bool motion;

  /// 正在执行的运动：车在动。
  bool get moving =>
      motion && const ['queued', 'running', 'verifying'].contains(status);
}

enum DemoScenario {
  normal,
  silence,
  asrFailure,
  synthesisFailure,
  requestFailure,
}

/// 页面可见的设备连接状态。
///
/// [connecting] 表示首屏连接结果尚未回来：此时既不能显示「连接正常」，
/// 也不该判成「未连接」，否则页面会在启动阶段报错误状态。
enum DeviceConnection { connecting, connected, disconnected }

/// 用于模拟数据源与页面之间的明确失败。
/// 机器人运行设置（合同 SettingsResponse）：场景提示词与免按键对话、主动观察开关。
class RobotSettings {
  const RobotSettings({
    this.scenarioPrompt = '',
    this.handsFree = false,
    this.proactive = false,
    this.handsFreeAvailable = false,
    this.proactiveAvailable = false,
  });

  factory RobotSettings.fromJson(Map<String, dynamic> json) => RobotSettings(
    scenarioPrompt: json['scenario_prompt']?.toString() ?? '',
    handsFree: json['hands_free'] == true,
    proactive: json['proactive'] == true,
    handsFreeAvailable: json['hands_free_available'] == true,
    proactiveAvailable: json['proactive_available'] == true,
  );

  /// 场景提示词上限（合同 0–500 字）。
  static const int maxPromptChars = 500;

  final String scenarioPrompt;
  final bool handsFree;
  final bool proactive;
  final bool handsFreeAvailable;
  final bool proactiveAvailable;
}

class ClassroomFailure implements Exception {
  const ClassroomFailure(this.message);
  final String message;
  @override
  String toString() => message;
}
