// frontend/lib/features/classroom/classroom_service.dart
import 'session_model.dart';

/// 页面内部的数据源边界；字段语义对齐 API_CONTRACT.md 的 HTTP 合同。
///
/// 实现有两路：MockClassroomService（本地演示）与 DeviceClassroomService
/// （Jetson 设备端 /api/v1）。带 kind/question_id 的提交与合同的输入/录音
/// 通道一一对应；地址类能力返回 null 表示该数据源不支持。
abstract interface class ClassroomService {
  Stream<SessionModel> get events;
  Stream<bool> get connections;

  /// 初始化或断线重连时的聚合快照（合同 GET /tasks/{id}/session）。
  Future<SessionModel> snapshot();

  /// 创建教学任务（合同 POST /tasks）；topicId 省略由核心选择主题。
  Future<void> createTask(String target, {String? topicId});

  /// 提交学生输入（合同 POST /tasks/{id}/inputs）。
  ///
  /// [kind] 为 question 或 answer；answer 必须携带 [questionId] 匹配当前
  /// 待答问题。语音转写最终文本由服务端提交，前端不得再转发。
  Future<void> submitText(
    String text, {
    String kind = 'question',
    String? questionId,
  });

  /// 开始录音（合同 POST /tasks/{id}/recordings）。
  ///
  /// [kind]/[questionId] 与文字输入同语义；[maxDurationS] 到时自动结束。
  Future<void> startRecording({
    String kind = 'question',
    String? questionId,
    double maxDurationS = 15,
  });

  /// 结束录音并触发识别（合同 PATCH recordings，status=transcribing）。
  Future<void> finishRecording();

  /// 停止任务（合同 PATCH /tasks/{id}，status=cancelling）。
  Future<void> stopTask();

  /// 请求一次场景观察（合同 POST /tasks/{id}/observations）。
  Future<void> refreshObservation({
    String question = '请描述画面中的物体及其相对位置。',
  });

  /// 断线后重新拉取快照并恢复事件流。
  Future<void> reconnect();

  /// 仅演示数据源支持场景切换；真实数据源为空实现。
  void setScenario(DemoScenario scenario);

  /// 断开事件流与网络连接。
  void disconnect();

  void dispose();

  /// 摄像头快照地址（合同 GET /api/v1/camera/snapshot）；不支持返回 null。
  Uri? cameraSnapshotUrl({int? maxWidth});

  /// 摄像头 MJPEG 流地址（合同 GET /api/v1/camera/stream）；不支持返回 null。
  Uri? cameraStreamUrl({int fps = 10});
}
