// frontend/lib/features/robot_console/widgets/conversation_panel.dart
import 'package:flutter/material.dart';

import '../models/robot_console_models.dart';
import '../theme/console_theme.dart';
import 'common.dart';

/// 对话与语音面板：消息列表、录音状态条与底部输入行。
class ConsoleConversationPanel extends StatelessWidget {
  const ConsoleConversationPanel({
    required this.session,
    required this.busy,
    required this.messageController,
    required this.onSend,
    required this.onRecord,
    super.key,
  });

  /// 当前页面级会话快照。
  final RobotSession session;

  /// 控制器正忙（请求进行中）。
  final bool busy;

  /// 文字输入控制器，由页面持有与释放。
  final TextEditingController messageController;

  /// 发送文字回调。
  final VoidCallback onSend;

  /// 录音开始/结束切换回调。
  final VoidCallback onRecord;

  bool get _recording => session.recordingStatus == 'recording';

  bool get _transcribing => session.recordingStatus == 'transcribing';

  /// 秒数格式化为 mm:ss。
  static String _formatSeconds(int seconds) {
    final int minutes = seconds ~/ 60;
    final int rest = seconds % 60;
    return '${minutes.toString().padLeft(2, '0')}:'
        '${rest.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) => Container(
    decoration: consolePanelDecoration(),
    padding: const EdgeInsets.all(ConsoleSpace.cardPadding),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        ConsoleSectionHeader(
          title: '对话与语音',
          trailing: ConsoleStatusChip(
            label: consoleStatusText(session.recordingStatus),
            status: session.recordingStatus,
          ),
        ),
        const SizedBox(height: ConsoleSpace.itemGap),
        Expanded(
          child: session.hasTask
              ? _MessageList(session: session)
              : const ConsoleEmptyHint(
                  icon: Icons.chat_bubble_outline,
                  text: '请先创建任务，再开始对话',
                ),
        ),
        if (_recording || _transcribing) ...[
          const SizedBox(height: ConsoleSpace.itemGap),
          _RecordingBar(
            status: session.recordingStatus,
            seconds: session.recordingSeconds,
            maxSeconds: session.maxRecordingSeconds,
          ),
        ],
        const SizedBox(height: ConsoleSpace.itemGap),
        _inputRow(),
      ],
    ),
  );

  Widget _inputRow() {
    // 发送：任务未活动、忙或音频占用时禁用，与服务端校验保持一致；
    // 终态任务的迟到输入会被数据源拒绝，因此这里直接禁用。
    final bool canSend = session.taskActive && !busy && !session.audioBusy;
    // 录音：录音中始终可结束；否则需任务活动且未处理中、音频空闲。
    final bool canRecord =
        _recording ||
        (session.taskActive && !session.audioBusy && !session.processing);
    final String recordTip = _recording
        ? '结束录音并识别'
        : !session.taskActive
        ? '请先创建任务'
        : (session.audioBusy || session.processing)
        ? '处理中或音频占用时不能录音'
        : '开始录音';
    return Row(
      children: [
        Expanded(
          child: TextField(
            key: const Key('message_input'),
            controller: messageController,
            enabled: canSend,
            minLines: 1,
            maxLines: 3,
            maxLength: 4000,
            decoration: const InputDecoration(
              hintText: '输入问题或指令…',
              counterText: '',
            ),
            onSubmitted: (_) => onSend(),
          ),
        ),
        const SizedBox(width: ConsoleSpace.itemGap),
        SizedBox(
          height: ConsoleSpace.buttonHeight,
          child: FilledButton(
            key: const Key('send_button'),
            onPressed: canSend ? onSend : null,
            child: const Text('发送'),
          ),
        ),
        const SizedBox(width: ConsoleSpace.itemGap),
        Tooltip(
          message: recordTip,
          child: SizedBox(
            height: ConsoleSpace.buttonHeight,
            child: OutlinedButton.icon(
              key: const Key('record_button'),
              onPressed: canRecord ? onRecord : null,
              icon: Icon(_recording ? Icons.stop : Icons.mic_none, size: 18),
              label: Text(_recording ? '结束录音' : '录音'),
            ),
          ),
        ),
      ],
    );
  }
}

/// 消息列表，空列表时给出提示。
class _MessageList extends StatelessWidget {
  const _MessageList({required this.session});

  final RobotSession session;

  @override
  Widget build(BuildContext context) {
    if (session.messages.isEmpty) {
      return const ConsoleEmptyHint(
        icon: Icons.forum_outlined,
        text: '暂无消息，输入问题或开始录音',
      );
    }
    return ListView.builder(
      itemCount: session.messages.length,
      itemBuilder: (BuildContext context, int index) {
        final ChatMessage message = session.messages[index];
        return _FadeIn(
          key: ValueKey<String>(message.messageId),
          child: _MessageBubble(message: message, actions: session.actions),
        );
      },
    );
  }
}

/// 单条消息气泡：user 右侧浅绿、assistant 左侧白底边框、system 居中灰条。
class _MessageBubble extends StatelessWidget {
  const _MessageBubble({required this.message, required this.actions});

  final ChatMessage message;
  final List<RobotAction> actions;

  /// 关联的 speak 动作，用于在 assistant 气泡下显示播报状态。
  RobotAction? get _speechAction {
    final int? id = message.speechActionId;
    if (id == null) return null;
    for (final RobotAction action in actions) {
      if (action.actionId == id) return action;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    if (message.role == 'system') {
      return Center(
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 4),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          decoration: BoxDecoration(
            color: ConsoleColors.panelSubtle,
            borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
          ),
          child: Text(message.text, style: ConsoleText.caption),
        ),
      );
    }
    final bool isUser = message.role == 'user';
    final RobotAction? speech = _speechAction;
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: const BoxConstraints(maxWidth: 520),
        decoration: BoxDecoration(
          color: isUser ? ConsoleColors.accentSoft : ConsoleColors.panel,
          border: isUser ? null : Border.all(color: ConsoleColors.border),
          borderRadius: BorderRadius.circular(ConsoleSpace.radius),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(message.text, style: ConsoleText.body),
            if (speech != null) ...[
              const SizedBox(height: 4),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.circle,
                    size: 7,
                    color: consoleStatusColor(speech.status),
                  ),
                  const SizedBox(width: 6),
                  Text(
                    '语音：${consoleStatusText(speech.status)}',
                    style: ConsoleText.caption,
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// 录音/识别状态条：录音中为呼吸红点加计时，识别中为转圈加文案。
class _RecordingBar extends StatelessWidget {
  const _RecordingBar({
    required this.status,
    required this.seconds,
    required this.maxSeconds,
  });

  final String status;
  final int seconds;
  final int maxSeconds;

  @override
  Widget build(BuildContext context) {
    final bool recording = status == 'recording';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: recording ? ConsoleColors.errorSoft : ConsoleColors.panelSubtle,
        borderRadius: BorderRadius.circular(ConsoleSpace.radiusSmall),
      ),
      child: Row(
        children: [
          if (recording)
            const _BreathingDot()
          else
            const SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
          const SizedBox(width: 8),
          Text(
            recording
                ? '${ConsoleConversationPanel._formatSeconds(seconds)} / '
                      '最长 $maxSeconds 秒'
                : '识别中…',
            style: recording
                ? ConsoleText.bodySmall.copyWith(color: ConsoleColors.error)
                : ConsoleText.bodySmall,
          ),
        ],
      ),
    );
  }
}

/// 录音中的红色呼吸圆点，退出时释放动画控制器。
class _BreathingDot extends StatefulWidget {
  const _BreathingDot();

  @override
  State<_BreathingDot> createState() => _BreathingDotState();
}

class _BreathingDotState extends State<_BreathingDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => FadeTransition(
    opacity: _controller,
    child: const Icon(Icons.circle, size: 10, color: ConsoleColors.error),
  );
}

/// 新消息淡入包装：插入时从透明过渡到不透明。
class _FadeIn extends StatefulWidget {
  const _FadeIn({required this.child, super.key});

  final Widget child;

  @override
  State<_FadeIn> createState() => _FadeInState();
}

class _FadeInState extends State<_FadeIn> {
  double _opacity = 0;

  @override
  void initState() {
    super.initState();
    // 首帧后再置 1，确保 AnimatedOpacity 产生过渡而不是瞬显。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) setState(() => _opacity = 1);
    });
  }

  @override
  Widget build(BuildContext context) => AnimatedOpacity(
    opacity: _opacity,
    duration: const Duration(milliseconds: 250),
    child: widget.child,
  );
}
