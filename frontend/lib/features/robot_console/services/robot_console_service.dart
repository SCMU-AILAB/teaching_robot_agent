// frontend/lib/features/robot_console/services/robot_console_service.dart
import '../models/robot_console_models.dart';

/// 页面数据源边界；后续由 HttpRobotConsoleService 按审核后的 API_CONTRACT.md 实现。
abstract interface class RobotConsoleService {
  /// 会话事件流，携带 instanceId 与 sequence 供去重。
  Stream<RobotSession> get events;

  /// 连接状态流，true 表示在线。
  Stream<bool> get connections;

  /// 获取当前完整快照。
  Future<RobotSession> snapshot();

  /// 创建任务，目标为 1–2000 字。
  Future<void> createTask(String target);

  /// 提交文字输入，内容为 1–4000 字。
  Future<void> submitText(String text);

  /// 验收当前任务。
  Future<void> finishTask();

  /// 请求停止当前任务并确认资源释放。
  Future<void> stopTask();

  /// 开始录音。
  Future<void> startRecording();

  /// 结束录音并进入识别。
  Future<void> finishRecording();

  /// 主动刷新观察结果。
  Future<void> refreshObservation();

  /// 断线后重新连接并准备恢复快照。
  Future<void> reconnect();

  /// 切换演示场景，驱动 Mock 分支行为。
  void setScenario(ConsoleScenario scenario);

  /// 模拟断线。
  void disconnect();

  /// 释放服务资源。
  void dispose();
}
