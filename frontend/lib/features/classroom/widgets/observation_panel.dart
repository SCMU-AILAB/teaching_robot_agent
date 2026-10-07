// frontend/lib/features/classroom/widgets/observation_panel.dart
import 'package:flutter/material.dart';

import '../session_model.dart';

/// 展示明确标记的示例场景和观察，不冒充真实摄像头画面。
class ObservationPanel extends StatelessWidget {
  const ObservationPanel({
    required this.state,
    required this.onRefresh,
    super.key,
  });
  final SessionModel state;
  final VoidCallback? onRefresh;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Row(
        children: [
          const Icon(Icons.videocam_outlined, size: 21),
          const SizedBox(width: 9),
          Text('观察视野', style: Theme.of(context).textTheme.titleMedium),
          const Spacer(),
          IconButton(
            onPressed: onRefresh,
            tooltip: '刷新观察',
            icon: const Icon(Icons.refresh, size: 20),
          ),
        ],
      ),
      const SizedBox(height: 8),
      AspectRatio(
        aspectRatio: 16 / 10,
        child: Stack(
          fit: StackFit.expand,
          children: [
            Image.asset(
              'assets/scene.png',
              fit: BoxFit.cover,
              semanticLabel: '模拟桌面：绿色杯子在蓝色书左侧，橙色方块在右侧',
              errorBuilder: (_, _, _) => const ColoredBox(
                color: Color(0xffe8eded),
                child: Center(child: Text('场景图片加载失败')),
              ),
            ),
            const Positioned(
              left: 12,
              top: 12,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.all(Radius.circular(4)),
                ),
                child: Padding(
                  padding: EdgeInsets.symmetric(horizontal: 9, vertical: 4),
                  child: Text('示例场景 · 非实时摄像头', style: TextStyle(fontSize: 11)),
                ),
              ),
            ),
          ],
        ),
      ),
      const SizedBox(height: 18),
      Row(
        children: [
          const Text('场景观察', style: TextStyle(fontWeight: FontWeight.w600)),
          const Spacer(),
          if (state.observed)
            Text(
              state.stale ? '观察已过期' : '模拟结果',
              style: const TextStyle(fontSize: 12, color: Color(0xff73817b)),
            ),
        ],
      ),
      const SizedBox(height: 8),
      Text(state.observation, style: const TextStyle(height: 1.8)),
    ],
  );
}
