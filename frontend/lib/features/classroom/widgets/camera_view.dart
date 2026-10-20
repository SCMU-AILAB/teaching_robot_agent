// frontend/lib/features/classroom/widgets/camera_view.dart
import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

import '../../../core/constants/app_radius.dart';
import 'camera_view_io.dart'
    if (dart.library.js_interop) 'camera_view_web.dart'
    as platform;

/// 设备摄像头实时画面。
///
/// Web 端把 MJPEG 流交给浏览器 `<img>` 原生解码（CPU 占用低、延迟最小），
/// 其余平台无法解码 multipart 流，改为按帧轮询单帧快照。
///
/// [streamUrl] 为空表示当前数据源没有摄像头（例如纯内存测试替身），
/// 直接显示 [CameraUnavailable]，不做任何网络请求。
class CameraView extends StatelessWidget {
  const CameraView({
    required this.streamUrl,
    this.snapshotUrl,
    this.fps = 10,
    this.borderRadius = AppRadius.media,
    this.unavailable = const CameraUnavailable(),
    super.key,
  });

  /// MJPEG 地址（合同 GET /api/v1/camera/stream）。
  final Uri? streamUrl;

  /// 单帧地址（合同 GET /api/v1/camera/snapshot），非 Web 平台轮询使用。
  final Uri? snapshotUrl;

  /// 推流帧率，同时作为非 Web 平台的轮询上限。
  final int fps;

  /// 画面圆角：Web 平台视图不受 ClipRRect 裁剪，需下沉到元素样式。
  final double borderRadius;

  /// 无画面时的占位内容。
  final Widget unavailable;

  @override
  Widget build(BuildContext context) {
    final Uri? stream = streamUrl;
    if (stream == null) return unavailable;
    return platform.buildCameraView(
      streamUrl: stream,
      snapshotUrl: snapshotUrl,
      fps: fps,
      borderRadius: borderRadius,
      unavailable: unavailable,
    );
  }
}

/// 未连接服务器（或无画面）时的占位：与实时画面同一尺寸与圆角。
class CameraUnavailable extends StatelessWidget {
  const CameraUnavailable({super.key});

  @override
  Widget build(BuildContext context) => const ColoredBox(
    color: Color(0xffe8eded),
    child: Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Symbols.cloud_off_rounded,
            size: 38,
            color: Color(0xff93a29c),
          ),
          SizedBox(height: 12),
          Text(
            '未连接服务器',
            style: TextStyle(
              fontSize: 15,
              fontWeight: FontWeight.w600,
              color: Color(0xff55635e),
            ),
          ),
          SizedBox(height: 5),
          Text(
            '检查设备服务地址与网络后重试',
            style: TextStyle(fontSize: 12, color: Color(0xff73817b)),
          ),
        ],
      ),
    ),
  );
}
