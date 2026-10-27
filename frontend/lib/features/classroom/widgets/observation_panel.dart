// frontend/lib/features/classroom/widgets/observation_panel.dart
import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

import '../../../core/constants/app_radius.dart';
import '../session_model.dart';
import 'camera_view.dart';

/// 展示设备摄像头实时画面与真实观察结果。
///
/// 画面来自 Jetson 设备端 MJPEG 流；连接结果未回来时显示「正在连接设备」，
/// 连不上时整块替换为未连接占位，观察文本一并显示真实状态，不用任何
/// 本地示例图或示例文案。
class ObservationPanel extends StatelessWidget {
  const ObservationPanel({
    required this.state,
    required this.connection,
    this.streamUrl,
    this.snapshotUrl,
    this.onRefresh,
    super.key,
  });

  final SessionModel state;

  /// 设备端连接状态：决定画面、观察文本与刷新入口是否可用。
  final DeviceConnection connection;

  /// 摄像头 MJPEG 地址；v1.0 没有该接口，为空时轮询单帧快照。
  final Uri? streamUrl;

  /// 摄像头单帧地址（合同 GET /api/v1/camera/snapshot）。
  final Uri? snapshotUrl;

  final VoidCallback? onRefresh;

  bool get _connected => connection == DeviceConnection.connected;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Row(
        children: [
          const Icon(Symbols.videocam_rounded, size: 21),
          const SizedBox(width: 9),
          Text('观察视野', style: Theme.of(context).textTheme.titleMedium),
          if (_connected) ...[const SizedBox(width: 10), _sourceChip()],
          const Spacer(),
          IconButton(
            onPressed: onRefresh,
            tooltip: '刷新观察',
            icon: const Icon(Symbols.refresh_rounded, size: 20),
          ),
        ],
      ),
      const SizedBox(height: 8),
      AspectRatio(
        aspectRatio: 16 / 10,
        child: ClipRRect(
          borderRadius: BorderRadius.circular(AppRadius.media),
          child: switch (connection) {
            DeviceConnection.connected => CameraView(
              streamUrl: streamUrl,
              snapshotUrl: snapshotUrl,
              // 服务器通了但没有帧：问题在摄像头，不能报成「未连接服务器」。
              unavailable: const CameraUnavailable(
                title: '摄像头未就绪',
                detail: '设备已连接，等待摄像头出图',
              ),
            ),
            DeviceConnection.connecting => const CameraUnavailable(
              title: '正在连接设备…',
              detail: '首帧到达后自动开始播放',
            ),
            DeviceConnection.disconnected => const CameraUnavailable(),
          },
        ),
      ),
      const SizedBox(height: 18),
      Row(
        children: [
          const Text('场景观察', style: TextStyle(fontWeight: FontWeight.w600)),
          const Spacer(),
          if (connection == DeviceConnection.disconnected)
            const Text(
              '未连接服务器',
              style: TextStyle(fontSize: 12, color: Color(0xffb6403a)),
            )
          else if (_connected && state.observed)
            Text(
              state.stale ? '观察已过期' : '设备观察',
              style: const TextStyle(fontSize: 12, color: Color(0xff73817b)),
            ),
        ],
      ),
      const SizedBox(height: 8),
      Text(switch (connection) {
        DeviceConnection.connecting => '正在获取设备数据，请稍候…',
        DeviceConnection.connected => state.observation,
        DeviceConnection.disconnected => '未连接服务器，暂无观察结果。',
      }, style: const TextStyle(height: 1.8)),
    ],
  );

  /// 画面来源标签：只说明画面来自设备摄像头。
  ///
  /// 不写「实时」——连接正常但摄像头没出图时那句会自相矛盾，出没出画由
  /// 画面本身与占位文案表达。
  Widget _sourceChip() => Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
    decoration: ShapeDecoration(
      shape: AppRadius.containerShape(radius: AppRadius.chip),
      color: const Color(0xffe6eee9),
    ),
    child: const Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Symbols.videocam_rounded, size: 12, color: Color(0xff147d64)),
        SizedBox(width: 5),
        Text('设备摄像头', style: TextStyle(fontSize: 11, color: Color(0xff147d64))),
      ],
    ),
  );
}
