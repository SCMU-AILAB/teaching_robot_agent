// frontend/lib/features/classroom/widgets/map_panel.dart
import 'dart:async';
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

import '../../../core/constants/app_radius.dart';
import '../session_model.dart';

const Color _unknownColor = Color(0xff2b3033);
const Color _freeColor = Color(0xffe4e9eb);
const Color _occupiedColor = Color(0xffd0453c);
const Color _robotColor = Color(0xfff06a30);
const Color _homeColor = Color(0xff147d64);

/// 实时地图（合同 GET /api/v1/map）：每秒读取一次，连接断开时暂停。
///
/// 后端没有深度相机时返回 50002，面板显示原因并放慢到每 10 秒重试一次，
/// 不在页面上反复报错。
class MapPanel extends StatefulWidget {
  const MapPanel({
    required this.load,
    required this.connection,
    this.interval = const Duration(seconds: 1),
    super.key,
  });

  final Future<MapSnapshot> Function() load;

  /// 连接中与未连接都不请求；连接中不显示“未连接”。
  final DeviceConnection connection;

  bool get connected => connection == DeviceConnection.connected;
  final Duration interval;

  @override
  State<MapPanel> createState() => _MapPanelState();
}

class _MapPanelState extends State<MapPanel> {
  MapSnapshot? _map;
  ui.Image? _image;
  String? _error;
  Timer? _timer;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    if (widget.connected) _schedule(Duration.zero);
  }

  @override
  void didUpdateWidget(MapPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.connected && !oldWidget.connected) _schedule(Duration.zero);
    if (!widget.connected) {
      _timer?.cancel();
      _timer = null;
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    _image?.dispose();
    super.dispose();
  }

  void _schedule(Duration delay) {
    _timer?.cancel();
    _timer = Timer(delay, () => unawaited(_refresh()));
  }

  Future<void> _refresh() async {
    if (!mounted || _loading || !widget.connected) return;
    _loading = true;
    Duration next = widget.interval;
    try {
      final MapSnapshot map = await widget.load();
      final ui.Image image = await _render(map);
      if (!mounted) {
        image.dispose();
        return;
      }
      final ui.Image? previous = _image;
      setState(() {
        _map = map;
        _image = image;
        _error = null;
      });
      previous?.dispose();
    } on ClassroomFailure catch (failure) {
      next = widget.interval * 10;
      if (mounted) setState(() => _error = failure.message);
    } catch (_) {
      if (mounted) setState(() => _error = '读不到地图。');
    } finally {
      _loading = false;
      if (mounted && widget.connected) _schedule(next);
    }
  }

  /// 格子转为 RGBA 像素图；第 0 行 y 最小，画在最下面。
  static Future<ui.Image> _render(MapSnapshot map) {
    final Uint8List pixels = Uint8List(map.width * map.height * 4);
    for (int row = 0; row < map.height; row++) {
      final int target = map.height - 1 - row;
      for (int col = 0; col < map.width; col++) {
        final Color color = switch (map.cells[row * map.width + col]) {
          MapSnapshot.free => _freeColor,
          MapSnapshot.occupied => _occupiedColor,
          _ => _unknownColor,
        };
        final int offset = (target * map.width + col) * 4;
        pixels[offset] = (color.r * 255).round();
        pixels[offset + 1] = (color.g * 255).round();
        pixels[offset + 2] = (color.b * 255).round();
        pixels[offset + 3] = 255;
      }
    }
    final Completer<ui.Image> done = Completer<ui.Image>();
    ui.decodeImageFromPixels(
      pixels,
      map.width,
      map.height,
      ui.PixelFormat.rgba8888,
      done.complete,
    );
    return done.future;
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Row(
        children: [
          const Icon(Symbols.map_rounded, size: 21),
          const SizedBox(width: 9),
          Text('地图', style: Theme.of(context).textTheme.titleMedium),
          const Spacer(),
          if (_map != null)
            Text(
              '每格 ${(_map!.resolutionM * 100).round()} 厘米',
              style: const TextStyle(fontSize: 12, color: Color(0xff73817b)),
            ),
        ],
      ),
      const SizedBox(height: 8),
      AspectRatio(
        aspectRatio: 16 / 10,
        child: ClipRRect(
          borderRadius: BorderRadius.circular(AppRadius.control),
          child: ColoredBox(
            color: _unknownColor,
            child: _map != null && _image != null
                ? CustomPaint(
                    key: const Key('map-canvas'),
                    painter: _MapPainter(_map!, _image!),
                    size: Size.infinite,
                  )
                : Center(
                    child: Text(
                      switch (widget.connection) {
                        DeviceConnection.connecting => '正在连接设备…',
                        DeviceConnection.disconnected => '未连接服务器',
                        DeviceConnection.connected => _error ?? '正在读取地图…',
                      },
                      key: const Key('map-status'),
                      style: const TextStyle(color: Color(0xffb7c2c0)),
                    ),
                  ),
          ),
        ),
      ),
      const SizedBox(height: 10),
      const Wrap(
        spacing: 16,
        runSpacing: 6,
        children: [
          _Legend(_robotColor, '机器人', round: true),
          _Legend(_homeColor, '出发点', round: true),
          _Legend(_freeColor, '看过的空地'),
          _Legend(_occupiedColor, '障碍物'),
          _Legend(_unknownColor, '还没看过'),
        ],
      ),
      const SizedBox(height: 6),
      const Text(
        '车停下时把看到的画上去；按轮子里程推算位置，走远了会有些偏。',
        style: TextStyle(fontSize: 12, color: Color(0xff73817b)),
      ),
    ],
  );
}

class _Legend extends StatelessWidget {
  const _Legend(this.color, this.label, {this.round = false});
  final Color color;
  final String label;
  final bool round;

  @override
  Widget build(BuildContext context) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      Container(
        width: 12,
        height: 12,
        decoration: BoxDecoration(
          color: color,
          shape: round ? BoxShape.circle : BoxShape.rectangle,
          border: Border.all(color: const Color(0xffcfd8d6)),
        ),
      ),
      const SizedBox(width: 6),
      Text(label, style: const TextStyle(fontSize: 12)),
    ],
  );
}

class _MapPainter extends CustomPainter {
  _MapPainter(this.map, this.image);
  final MapSnapshot map;
  final ui.Image image;

  @override
  void paint(Canvas canvas, Size size) {
    final double scale = math.min(
      size.width / map.width,
      size.height / map.height,
    );
    final Rect frame = Rect.fromLTWH(
      (size.width - map.width * scale) / 2,
      (size.height - map.height * scale) / 2,
      map.width * scale,
      map.height * scale,
    );
    canvas.drawImageRect(
      image,
      Rect.fromLTWH(0, 0, map.width.toDouble(), map.height.toDouble()),
      frame,
      Paint()..filterQuality = FilterQuality.none,
    );
    Offset toScreen(double x, double y) => Offset(
      frame.left + (x - map.originX) / map.resolutionM * scale,
      frame.top + (map.height - (y - map.originY) / map.resolutionM) * scale,
    );
    final MapPose? home = map.home;
    if (home != null) {
      canvas.drawCircle(
        toScreen(home.x, home.y),
        8,
        Paint()
          ..color = _homeColor
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3,
      );
    }
    final Offset robot = toScreen(map.robot.x, map.robot.y);
    // 屏幕 y 向下：朝向的 y 分量取反
    final Offset nose =
        robot + Offset(math.cos(map.robot.yaw), -math.sin(map.robot.yaw)) * 18;
    canvas.drawLine(
      robot,
      nose,
      Paint()
        ..color = _robotColor
        ..strokeWidth = 3
        ..strokeCap = StrokeCap.round,
    );
    canvas.drawCircle(robot, 7, Paint()..color = _robotColor);
  }

  @override
  bool shouldRepaint(_MapPainter oldDelegate) =>
      oldDelegate.map != map || oldDelegate.image != image;
}
