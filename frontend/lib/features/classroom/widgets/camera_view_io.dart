// frontend/lib/features/classroom/widgets/camera_view_io.dart
import 'dart:async';

import 'package:flutter/material.dart';

/// 非 Web 平台：无法解码 multipart MJPEG，按帧轮询单帧快照。
///
/// 轮询间隔取 1/fps 并限制在 200–1000ms：既不会把设备端打满，也能保持
/// 画面可用；`gaplessPlayback` 保证换帧时不出黑屏。
Widget buildCameraView({
  required Uri streamUrl,
  required Uri? snapshotUrl,
  required int fps,
  required double borderRadius,
  required Widget unavailable,
}) => _SnapshotCameraView(
  snapshotUrl: snapshotUrl,
  fps: fps,
  borderRadius: borderRadius,
  unavailable: unavailable,
);

class _SnapshotCameraView extends StatefulWidget {
  const _SnapshotCameraView({
    required this.snapshotUrl,
    required this.fps,
    required this.borderRadius,
    required this.unavailable,
  });

  final Uri? snapshotUrl;
  final int fps;
  final double borderRadius;
  final Widget unavailable;

  @override
  State<_SnapshotCameraView> createState() => _SnapshotCameraViewState();
}

class _SnapshotCameraViewState extends State<_SnapshotCameraView> {
  Timer? _timer;
  int _nonce = 0;

  @override
  void initState() {
    super.initState();
    if (widget.snapshotUrl == null) return;
    final int millis = (1000 / widget.fps).round().clamp(200, 1000);
    _timer = Timer.periodic(Duration(milliseconds: millis), (_) {
      if (mounted) setState(() => _nonce += 1);
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final Uri? base = widget.snapshotUrl;
    if (base == null) return widget.unavailable;
    // 每次换帧加时间戳，绕过缓存拿到最新一帧。
    final Uri frame = base.replace(
      queryParameters: <String, String>{
        ...base.queryParameters,
        'max_width': '960',
        'frame': '$_nonce',
      },
    );
    return Image.network(
      frame.toString(),
      fit: BoxFit.cover,
      gaplessPlayback: true,
      filterQuality: FilterQuality.low,
      errorBuilder: (_, _, _) => widget.unavailable,
    );
  }
}
