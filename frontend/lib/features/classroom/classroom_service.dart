// frontend/lib/features/classroom/classroom_service.dart
import 'session_model.dart';

/// 页面内部的数据源边界，后续由核心负责人提供已审核的网络适配。
abstract interface class ClassroomService {
  Stream<SessionModel> get events;
  Stream<bool> get connections;
  Future<SessionModel> snapshot();
  Future<void> createTask(String target);
  Future<void> submitText(String text);
  Future<void> startRecording();
  Future<void> finishRecording();
  Future<void> stopTask();
  Future<void> refreshObservation();
  Future<void> reconnect();
  void setScenario(DemoScenario scenario);
  void disconnect();
  void dispose();
}
