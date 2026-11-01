// frontend/lib/features/classroom/widgets/settings_dialog.dart
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:material_symbols_icons/symbols.dart';

import '../session_model.dart';

/// 读取设置；失败抛 [ClassroomFailure]。
typedef SettingsLoader = Future<RobotSettings> Function();

/// 保存设置；只提交非空参数，失败抛 [ClassroomFailure]。
typedef SettingsSaver =
    Future<RobotSettings> Function({
      String? scenarioPrompt,
      bool? handsFree,
      bool? proactive,
    });

/// 机器人设置框：场景提示词（下一句生效）、免按键对话与主动观察开关（合同 /settings）。
class SettingsDialog extends StatefulWidget {
  const SettingsDialog({required this.load, required this.save, super.key});

  final SettingsLoader load;
  final SettingsSaver save;

  @override
  State<SettingsDialog> createState() => _SettingsDialogState();
}

class _SettingsDialogState extends State<SettingsDialog> {
  final TextEditingController _prompt = TextEditingController();
  RobotSettings? _loaded;
  bool _handsFree = false;
  bool _proactive = false;
  bool _busy = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void dispose() {
    _prompt.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final RobotSettings settings = await widget.load();
      if (!mounted) return;
      setState(() {
        _loaded = settings;
        _prompt.text = settings.scenarioPrompt;
        _handsFree = settings.handsFree;
        _proactive = settings.proactive;
        _error = null;
      });
    } on ClassroomFailure catch (failure) {
      if (mounted) setState(() => _error = failure.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 只提交变化的字段；成功后关闭并返回新设置。
  Future<void> _save() async {
    final RobotSettings? loaded = _loaded;
    if (loaded == null) return;
    final String prompt = _prompt.text.trim();
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final RobotSettings saved = await widget.save(
        scenarioPrompt: prompt == loaded.scenarioPrompt ? null : prompt,
        handsFree: _handsFree == loaded.handsFree ? null : _handsFree,
        proactive: _proactive == loaded.proactive ? null : _proactive,
      );
      if (mounted) Navigator.of(context).pop(saved);
    } on ClassroomFailure catch (failure) {
      if (mounted) setState(() => _error = failure.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _switch({
    required Key key,
    required String title,
    required String subtitle,
    required bool value,
    required bool available,
    required ValueChanged<bool> onChanged,
  }) => SwitchListTile(
    key: key,
    contentPadding: EdgeInsets.zero,
    title: Text(title, style: const TextStyle(fontSize: 14)),
    subtitle: Text(
      available ? subtitle : '设备未配置该功能',
      style: const TextStyle(fontSize: 12, color: Color(0xff73817b)),
    ),
    value: value,
    onChanged: _busy || !available ? null : onChanged,
  );

  @override
  Widget build(BuildContext context) {
    final RobotSettings? loaded = _loaded;
    return AlertDialog(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      title: const Row(
        children: [
          Icon(Symbols.tune_rounded, size: 20),
          SizedBox(width: 8),
          Text('机器人设置', style: TextStyle(fontSize: 17)),
        ],
      ),
      content: SizedBox(
        width: 460,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              key: const Key('settings-prompt'),
              controller: _prompt,
              enabled: !_busy && loaded != null,
              minLines: 3,
              maxLines: 6,
              maxLength: RobotSettings.maxPromptChars,
              decoration: const InputDecoration(
                labelText: '场景提示词',
                hintText: '例如：孩子叫乐乐，6 岁；今天在厨房，重点讲用电用火安全。',
                alignLabelWithHint: true,
              ),
            ),
            const Text(
              '接在系统提示之后，作用于回答、探索讲解和主动提醒，下一句生效；不能覆盖安全规则。',
              style: TextStyle(
                fontSize: 12,
                color: Color(0xff73817b),
                height: 1.5,
              ),
            ),
            const SizedBox(height: 8),
            _switch(
              key: const Key('settings-hands-free'),
              title: '免按键对话',
              subtitle: '有任务时一直听，说完一句就回答；机器人说话时不听。',
              value: _handsFree,
              available: loaded?.handsFreeAvailable ?? false,
              onChanged: (bool value) => setState(() => _handsFree = value),
            ),
            _switch(
              key: const Key('settings-proactive'),
              title: '主动观察',
              subtitle: '空闲时每 10 秒看一眼，发现剪刀、插座等就主动提醒。',
              value: _proactive,
              available: loaded?.proactiveAvailable ?? false,
              onChanged: (bool value) => setState(() => _proactive = value),
            ),
            if (_error != null) ...[
              const SizedBox(height: 8),
              Text(
                _error!,
                key: const Key('settings-error'),
                style: const TextStyle(fontSize: 12, color: Color(0xffb6403a)),
              ),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _busy ? null : () => Navigator.of(context).pop(),
          child: const Text('取消'),
        ),
        FilledButton(
          key: const Key('settings-save'),
          onPressed: _busy || loaded == null ? null : () => unawaited(_save()),
          child: Text(_busy && loaded != null ? '保存中…' : '保存'),
        ),
      ],
    );
  }
}
