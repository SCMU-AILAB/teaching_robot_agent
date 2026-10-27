// frontend/lib/features/classroom/widgets/camera_view_io.dart
import 'package:flutter/material.dart';

import 'camera_snapshot_view.dart';

/// 非 Web 平台：无法解码 multipart MJPEG，按帧轮询单帧快照。
Widget buildCameraView({
  required Uri streamUrl,
  required Uri? snapshotUrl,
  required int fps,
  required double borderRadius,
  required Widget unavailable,
}) => SnapshotCameraView(
  snapshotUrl: snapshotUrl,
  fps: fps,
  borderRadius: borderRadius,
  unavailable: unavailable,
);
