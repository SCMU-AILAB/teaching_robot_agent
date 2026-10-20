// frontend/lib/features/classroom/widgets/camera_view_web.dart
import 'dart:async';
import 'dart:js_interop';
import 'dart:ui_web' as ui_web;

import 'package:flutter/material.dart';
import 'package:web/web.dart' as web;

/// Web 端：MJPEG 交给浏览器 `<img>` 原生解码。
///
/// 首帧到达前显示占位；断流或长时间无帧时按固定间隔重新挂载地址。
/// 平台视图是真实 DOM 节点，Flutter 内容无法叠在其上，圆角通过元素样式
/// 设置，状态标签放在面板标题行（见 ObservationPanel）。
Widget buildCameraView({
  required Uri streamUrl,
  required Uri? snapshotUrl,
  required int fps,
  required double borderRadius,
  required Widget unavailable,
}) => _MjpegCameraView(
  streamUrl: streamUrl,
  fps: fps,
  borderRadius: borderRadius,
  unavailable: unavailable,
);

class _MjpegCameraView extends StatefulWidget {
  const _MjpegCameraView({
    required this.streamUrl,
    required this.fps,
    required this.borderRadius,
    required this.unavailable,
  });

  final Uri streamUrl;
  final int fps;
  final double borderRadius;
  final Widget unavailable;

  @override
  State<_MjpegCameraView> createState() => _MjpegCameraViewState();
}

class _MjpegCameraViewState extends State<_MjpegCameraView> {
  static const Duration _retryDelay = Duration(seconds: 3);
  static const Duration _firstFrameTimeout = Duration(seconds: 5);

  /// 视图工厂全局唯一编号：每个实例一个，避免复用同一 DOM 节点。
  static int _viewSeed = 0;

  late final String _viewType;
  late final web.HTMLImageElement _image;
  Timer? _probe;
  Timer? _watchdog;
  int _attempt = 0;

  /// 是否已解出至少一帧；false 时显示占位而不是黑框。
  bool _live = false;

  @override
  void initState() {
    super.initState();
    _viewSeed += 1;
    _viewType = 'teaching-robot-camera-$_viewSeed';
    _image = web.HTMLImageElement()
      ..setAttribute('alt', '设备摄像头实时画面');
    _image.style
      ..setProperty('width', '100%')
      ..setProperty('height', '100%')
      ..setProperty('object-fit', 'cover')
      ..setProperty('display', 'block')
      ..setProperty('border-radius', '${widget.borderRadius}px');
    _image.addEventListener('error', _onStreamError.toJS);
    ui_web.platformViewRegistry.registerViewFactory(
      _viewType,
      (int _) => _image,
    );
    _attach();
  }

  /// 挂载（或重新挂载）MJPEG 地址；每次带新参数强制重新建连。
  void _attach() {
    _attempt += 1;
    _image.src = widget.streamUrl
        .replace(
          queryParameters: <String, String>{
            ...widget.streamUrl.queryParameters,
            'fps': '${widget.fps}',
            'attempt': '$_attempt',
          },
        )
        .toString();
    _probe?.cancel();
    // 首帧到达后再判定是否连上：multipart 流的 load 事件在各浏览器语义不一致。
    _probe = Timer.periodic(const Duration(milliseconds: 400), (_) {
      if (!mounted) return;
      if (_image.naturalWidth > 0) {
        _probe?.cancel();
        _watchdog?.cancel();
        if (!_live) setState(() => _live = true);
      }
    });
    _watchdog?.cancel();
    _watchdog = Timer(_firstFrameTimeout, () {
      if (!mounted || _live) return;
      _probe?.cancel();
      _attach();
    });
  }

  void _onStreamError(web.Event event) {
    if (!mounted) return;
    _probe?.cancel();
    _watchdog?.cancel();
    if (_live) setState(() => _live = false);
    _watchdog = Timer(_retryDelay, () {
      if (mounted) _attach();
    });
  }

  @override
  void dispose() {
    _probe?.cancel();
    _watchdog?.cancel();
    _image.removeEventListener('error', _onStreamError.toJS);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => _live
      ? HtmlElementView(viewType: _viewType)
      : widget.unavailable;
}
