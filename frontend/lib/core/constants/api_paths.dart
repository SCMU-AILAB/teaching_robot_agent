// frontend/lib/core/constants/api_paths.dart

/// 后端 HTTP 路径，唯一依据为仓库根目录 API_CONTRACT.md v1.0。
///
/// 只包含相对 base URL 的路径；base URL 由运行时设置提供，不得硬编码主机地址。
abstract final class ApiPaths {
  /// 统一前缀.
  static const String prefix = '/api/v1';

  /// GET：设备与服务状态.
  static const String system = '$prefix/system';

  /// POST：创建任务.
  static const String tasks = '$prefix/tasks';

  /// GET：摄像头最近一帧 JPEG.
  static const String cameraSnapshot = '$prefix/camera/snapshot';

  /// GET：任务快照；PATCH：停止或确认完成.
  static String task(int taskId) => '$tasks/$taskId';

  /// POST：发送文字消息.
  static String messages(int taskId) => '${task(taskId)}/messages';

  /// POST：开始录音.
  static String recordings(int taskId) => '${task(taskId)}/recordings';

  /// PATCH：结束录音并识别，或放弃录音.
  static String recording(int taskId, String recordingId) =>
      '${recordings(taskId)}/${Uri.encodeComponent(recordingId)}';

  /// GET：任务事件流（SSE）.
  static String events(int taskId) => '${task(taskId)}/events';

  /// GET：已登记媒体内容.
  static String evidence(String evidenceId) =>
      '$prefix/evidence/${Uri.encodeComponent(evidenceId)}';
}
