// frontend/test/settings_dialog_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teaching_robot_frontend/features/classroom/session_model.dart';
import 'package:teaching_robot_frontend/features/classroom/widgets/settings_dialog.dart';

void main() {
  testWidgets('只提交改动过的字段；未配置的功能开关不可用', (WidgetTester tester) async {
    final List<Map<String, Object?>> saved = <Map<String, Object?>>[];
    RobotSettings? result;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (BuildContext context) => TextButton(
            onPressed: () async {
              result = await showDialog<RobotSettings>(
                context: context,
                builder: (_) => SettingsDialog(
                  load: () async => const RobotSettings(
                    scenarioPrompt: '旧提示',
                    handsFreeAvailable: true,
                  ),
                  save:
                      ({
                        String? scenarioPrompt,
                        bool? handsFree,
                        bool? proactive,
                      }) async {
                        saved.add(<String, Object?>{
                          'scenario_prompt': scenarioPrompt,
                          'hands_free': handsFree,
                          'proactive': proactive,
                        });
                        return RobotSettings(
                          scenarioPrompt: scenarioPrompt ?? '旧提示',
                          handsFree: handsFree ?? false,
                        );
                      },
                ),
              );
            },
            child: const Text('打开'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('打开'));
    await tester.pumpAndSettle();
    expect(find.text('旧提示'), findsOneWidget);
    final SwitchListTile proactive = tester.widget<SwitchListTile>(
      find.byKey(const Key('settings-proactive')),
    );
    expect(proactive.onChanged, isNull);
    await tester.tap(find.byKey(const Key('settings-hands-free')));
    await tester.pump();
    await tester.tap(find.byKey(const Key('settings-save')));
    await tester.pumpAndSettle();
    expect(saved.single, <String, Object?>{
      'scenario_prompt': null,
      'hands_free': true,
      'proactive': null,
    });
    expect(result?.handsFree, isTrue);
  });
}
