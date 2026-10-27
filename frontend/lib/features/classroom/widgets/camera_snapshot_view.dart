// frontend/lib/features/classroom/widgets/camera_snapshot_view.dart
import 'dart:async';

import 'package:flutter/material.dart';

/// 按帧轮询单帧快照（合同 GET /api/v1/camera/snapshot），各平台通用。
///
/// 轮询间隔取 1/fps 并限制在 200–1000ms：既不会把设备端打满，也能保持
/// 画面可用；`gaplessPlayback` 保证换帧时不出黑屏。
class SnapshotCameraView extends StatefulWidget {
  const SnapshotCameraView({
    super.key,
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
  State<SnapshotCameraView> createState() => SnapshotCameraViewState();
}

class SnapshotCameraViewState extends State<SnapshotCameraView> {
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
    // 每次换帧加时间戳，绕过缓存拿到最新一帧；宽度沿用调用方给的值。
    final Map<String, String> query = <String, String>{
      ...base.queryParameters,
      'frame': '$_nonce',
    };
    query.putIfAbsent('max_width', () => '800');
    return Image.network(
      base.replace(queryParameters: query).toString(),
      fit: BoxFit.cover,
      gaplessPlayback: true,
      filterQuality: FilterQuality.low,
      errorBuilder: (_, _, _) => widget.unavailable,
    );
  }
}
