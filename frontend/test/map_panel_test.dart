// frontend/test/map_panel_test.dart
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/features/classroom/session_model.dart';
import 'package:teaching_robot_frontend/features/classroom/widgets/map_panel.dart';

MapSnapshot sample() => MapSnapshot(
  resolutionM: 0.05,
  originX: -0.05,
  originY: -0.05,
  width: 3,
  height: 2,
  cells: Uint8List.fromList(const <int>[1, 1, 2, 0, 1, 2]),
  robot: const MapPose(0, 0, 0),
  home: const MapPose(0, 0, 0),
);

Widget host(Widget child) => MaterialApp(
  home: Scaffold(body: SizedBox(width: 480, child: child)),
);

void main() {
  testWidgets('读到地图后画出地图', (WidgetTester tester) async {
    int calls = 0;
    await tester.runAsync(() async {
      await tester.pumpWidget(
        host(
          MapPanel(
            load: () async {
              calls += 1;
              return sample();
            },
            connection: DeviceConnection.connected,
            interval: const Duration(hours: 1),
          ),
        ),
      );
      await Future<void>.delayed(const Duration(milliseconds: 200));
    });
    await tester.pump();
    expect(calls, 1);
    expect(find.byKey(const Key('map-canvas')), findsOneWidget);
    expect(find.text('每格 5 厘米'), findsOneWidget);
  });

  testWidgets('没有深度相机：显示原因，不画地图', (WidgetTester tester) async {
    await tester.runAsync(() async {
      await tester.pumpWidget(
        host(
          MapPanel(
            load: () async => throw const ClassroomFailure('未配置深度相机，没有地图'),
            connection: DeviceConnection.connected,
            interval: const Duration(hours: 1),
          ),
        ),
      );
      await Future<void>.delayed(const Duration(milliseconds: 50));
    });
    await tester.pump();
    expect(find.text('未配置深度相机，没有地图'), findsOneWidget);
    expect(find.byKey(const Key('map-canvas')), findsNothing);
  });

  testWidgets('未连接时不请求地图', (WidgetTester tester) async {
    int calls = 0;
    await tester.pumpWidget(
      host(
        MapPanel(
          load: () async {
            calls += 1;
            return sample();
          },
          connection: DeviceConnection.disconnected,
        ),
      ),
    );
    await tester.pump(const Duration(seconds: 3));
    expect(calls, 0);
    expect(find.text('未连接服务器'), findsOneWidget);
  });

  testWidgets('设为出发点：调用后显示结果；没有进行中的陪伴时按钮不可用', (WidgetTester tester) async {
    int calls = 0;
    await tester.pumpWidget(
      host(
        MapPanel(
          load: () async => throw const ClassroomFailure('未配置深度相机，没有地图'),
          connection: DeviceConnection.connected,
          setHome: () async {
            calls += 1;
            return const MapPose(0.5, 0, 0);
          },
          interval: const Duration(hours: 1),
        ),
      ),
    );
    await tester.pump();
    await tester.tap(find.byKey(const Key('map-set-home')));
    await tester.pump();
    await tester.pump();
    expect(calls, 1);
    expect(find.text('出发点已设为现在的位置。'), findsOneWidget);

    await tester.pumpWidget(
      host(
        MapPanel(
          load: () async => throw const ClassroomFailure('未配置深度相机，没有地图'),
          connection: DeviceConnection.connected,
          interval: const Duration(hours: 1),
        ),
      ),
    );
    final TextButton button = tester.widget<TextButton>(
      find.byKey(const Key('map-set-home')),
    );
    expect(button.onPressed, isNull);
    await tester.pumpWidget(const SizedBox());
  });
}
