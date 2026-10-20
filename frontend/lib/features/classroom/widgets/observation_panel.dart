// frontend/lib/features/classroom/widgets/observation_panel.dart
import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

import '../../../core/constants/app_radius.dart';
import '../session_model.dart';
import 'camera_view.dart';

/// 展示设备摄像头实时画面与真实观察结果。
///
/// 画面来自 Jetson 设备端 MJPEG 流；未连接服务器时整块替换为未连接占位，
/// 观察文本一并显示真实状态，不用任何本地示例图或示例文案。
class ObservationPanel extends StatelessWidget {
  const ObservationPanel({
    required this.state,
    required this.connected,
    this.streamUrl,
    this.snapshotUrl,
    this.onRefresh,
    super.key,
  });

  final SessionModel state;

  /// 设备端连接状态：决定画面、观察文本与刷新入口是否可用。
  final bool connected;

  /// 摄像头 MJPEG 地址（合同 GET /api/v1/camera/stream）。
  final Uri? streamUrl;

  /// 摄像头单帧地址（合同 GET /api/v1/camera/snapshot）。
  final Uri? snapshotUrl;

  final VoidCallback? onRefresh;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Row(
        children: [
          const Icon(Symbols.videocam_rounded, size: 21),
          const SizedBox(width: 9),
          Text('观察视野', style: Theme.of(context).textTheme.titleMedium),
          if (connected) ...[
            const SizedBox(width: 10),
            _liveChip(),
          ],
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
          child: connected
              ? CameraView(
                  streamUrl: streamUrl,
                  snapshotUrl: snapshotUrl,
                  unavailable: const CameraUnavailable(),
                )
              : const CameraUnavailable(),
        ),
      ),
      const SizedBox(height: 18),
      Row(
        children: [
          const Text('场景观察', style: TextStyle(fontWeight: FontWeight.w600)),
          const Spacer(),
          if (!connected)
            const Text(
              '未连接服务器',
              style: TextStyle(fontSize: 12, color: Color(0xffb6403a)),
            )
          else if (state.observed)
            Text(
              state.stale ? '观察已过期' : '设备观察',
              style: const TextStyle(fontSize: 12, color: Color(0xff73817b)),
            ),
        ],
      ),
      const SizedBox(height: 8),
      Text(
        connected ? state.observation : '未连接服务器，暂无观察结果。',
        style: const TextStyle(height: 1.8),
      ),
    ],
  );

  /// 实时画面标签：与页面其它角标同一套字号与配色。
  Widget _liveChip() => Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
    decoration: ShapeDecoration(
      shape: AppRadius.containerShape(radius: AppRadius.chip),
      color: const Color(0xffe6eee9),
    ),
    child: const Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Symbols.circle_rounded, size: 7, color: Color(0xff147d64)),
        SizedBox(width: 5),
        Text(
          '实时画面',
          style: TextStyle(fontSize: 11, color: Color(0xff147d64)),
        ),
      ],
    ),
  );
}
