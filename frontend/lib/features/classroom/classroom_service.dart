// frontend/lib/features/classroom/classroom_service.dart
import 'session_model.dart';

/// 页面内部的数据源边界；网络语义以根目录 API_CONTRACT.md v1.0 为准。
///
/// 实现有两路：MockClassroomService（测试替身）与 DeviceClassroomService
/// （机器人后端 /api/v1）。kind/question_id 为课堂演示保留，v1.0 不使用；
/// 地址类能力返回 null 表示该数据源不支持。
abstract interface class ClassroomService {
  Stream<SessionModel> get events;
  Stream<bool> get connections;

  /// 初始化或断线重连时的任务快照（合同 GET /tasks/{id}）。
  Future<SessionModel> snapshot();

  /// 创建教学任务（合同 POST /tasks）；topicId 省略由核心选择主题。
  Future<void> createTask(String target, {String? topicId});

  /// 提交文字（合同 POST /tasks/{id}/messages）。
  ///
  /// 语音转写最终文本由服务端登记为消息，前端不得再转发。
  Future<void> submitText(
    String text, {
    String kind = 'question',
    String? questionId,
  });

  /// 开始录音（合同 POST /tasks/{id}/recordings）。
  ///
  /// [maxDurationS] 到时自动结束。
  Future<void> startRecording({
    String kind = 'question',
    String? questionId,
    double maxDurationS = 15,
  });

  /// 结束录音并触发识别（合同 PATCH recordings，status=transcribing）。
  Future<void> finishRecording();

  /// 停止任务（合同 PATCH /tasks/{id}，status=cancelling）。
  Future<void> stopTask();

  /// 请机器人重新观察；v1.0 的观察由机器人发起，实现为一句提问。
  Future<void> refreshObservation({String question = '请描述画面中的物体及其相对位置。'});

  /// 断线后重新拉取快照并恢复事件流。
  Future<void> reconnect();

  /// 仅演示数据源支持场景切换；真实数据源为空实现。
  void setScenario(DemoScenario scenario);

  /// 断开事件流与网络连接。
  void disconnect();

  void dispose();

  /// 摄像头快照地址（合同 GET /api/v1/camera/snapshot）；不支持返回 null。
  Uri? cameraSnapshotUrl({int? maxWidth});

  /// 摄像头 MJPEG 流地址；v1.0 没有该接口，真实数据源返回 null 并改为轮询快照。
  Uri? cameraStreamUrl({int fps = 10});
}
