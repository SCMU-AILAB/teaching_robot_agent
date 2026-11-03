#include "screens/dashboard.h"
#include "text.h"
#include "ui_common.h"

#include <stdio.h>
#include <string.h>

static const char *task_stage(const App *app) {
    size_t index;
    SpeechPhase phase = state_speech_phase(&app->state);
    if (phase == SPEECH_RECORDING) return "正在听孩子说话";
    if (phase == SPEECH_TRANSCRIBING) return "正在识别语音";
    if (phase == SPEECH_PROCESSING) return "正在想怎么回答";
    if (phase == SPEECH_SPEAKING) return "正在说话";
    for (index = app->state.action_count; index > 0; index--) {
        const ActionRecord *action = &app->state.actions[index - 1];
        if (strcmp(action->status, "running") == 0 || strcmp(action->status, "verifying") == 0) {
            return skill_text(action->skill_name);
        }
    }
    if (!app->state.snapshot_loaded) return "等待开始";
    return task_status_text(app->state.task.status);
}

void dashboard_render(App *app, Rectangle content) {
    Rectangle left = {content.x, content.y, 286, 320};
    Rectangle center = {content.x + 296, content.y, 420, 320};
    Rectangle right = {content.x + 726, content.y, content.width - 726, 320};
    Rectangle events = {content.x, content.y + 330, content.width, content.height - 330};
    const float row_x = left.x + 14;
    const float row_w = left.width - 28;
    char value[256];
    char time_text[32];
    size_t index;
    ui_panel(left, "设备与服务", &app->theme);
    ui_status_row(&app->theme, (Rectangle){row_x, left.y + 60, row_w, 26}, "底盘",
                  app->state.system.robot_connected ? "已连接" : "未连接",
                  app->state.system.robot_connected ? COLOR_SUCCESS : COLOR_ERROR);
    ui_status_row(&app->theme, (Rectangle){row_x, left.y + 102, row_w, 26}, "后端",
                  app->state.backend_connected ? "已连接" : "离线",
                  app->state.backend_connected ? COLOR_SUCCESS : COLOR_ERROR);
    ui_status_row(&app->theme, (Rectangle){row_x, left.y + 144, row_w, 26}, "AI",
                  service_text(app->state.system.agent), ui_service_color(app->state.system.agent));
    ui_status_row(&app->theme, (Rectangle){row_x, left.y + 186, row_w, 26}, "摄像头",
                  app->state.system.camera_available ? "可用" : "不可用",
                  app->state.system.camera_available ? COLOR_SUCCESS : COLOR_ERROR);
    ui_status_row(&app->theme, (Rectangle){row_x, left.y + 228, row_w, 26}, "声音",
                  !app->state.system.audio_available ? "未配置" :
                  app->state.system.audio_recording ? "录音中" :
                  app->state.system.audio_playing ? "播放中" : "就绪",
                  app->state.system.audio_available ? COLOR_SUCCESS : COLOR_WARNING);
    ui_status_row(&app->theme, (Rectangle){row_x, left.y + 270, row_w, 26}, "执行层",
                  !app->state.backend_connected ? "未知" :
                  app->state.system.runtime_blocked_reason[0] == '\0' ? "正常" : "已锁定",
                  !app->state.backend_connected ? COLOR_WARNING :
                  app->state.system.runtime_blocked_reason[0] == '\0' ? COLOR_SUCCESS : COLOR_ERROR);

    ui_panel(center, "摄像头", &app->theme);
    camera_draw(&app->camera, (Rectangle){center.x + 12, center.y + 54, center.width - 24, 228},
                false, app->theme.body,
                app->state.system.camera_available ? "等待摄像头画面…" : "摄像头不可用");
    format_time(app->camera.live_updated_at > 0 ? app->camera.live_updated_at :
                app->state.system.camera_last_frame_at, time_text);
    (void)snprintf(value, sizeof(value), "最后更新 %s", time_text);
    ui_text(&app->theme, value, (Vector2){center.x + 14, center.y + 290}, 15, COLOR_MUTED);
    if (app->camera.error[0] != '\0') {
        ui_badge(&app->theme, (Rectangle){center.x + 270, center.y + 8, 136, 30}, "画面出错", COLOR_ERROR);
    }

    ui_panel(right, "当前任务", &app->theme);
    if (app->state.task.task_id > 0) {
        (void)snprintf(value, sizeof(value), "#%lld", (long long)app->state.task.task_id);
        ui_mono(&app->theme, value, (Vector2){right.x + 16, right.y + 60}, 24, COLOR_ACCENT);
        ui_badge(&app->theme, (Rectangle){right.x + right.width - 128, right.y + 56, 112, 34},
                 task_status_text(app->state.task.status), ui_service_color(app->state.task.status));
        ui_text(&app->theme, "今天的安排", (Vector2){right.x + 16, right.y + 104}, 15, COLOR_MUTED);
        ui_wrapped(&app->theme, app->state.task.user_target,
                   (Rectangle){right.x + 16, right.y + 128, right.width - 32, 56}, 17, 26, COLOR_TEXT);
        ui_text(&app->theme, "现在在做", (Vector2){right.x + 16, right.y + 194}, 15, COLOR_MUTED);
        ui_wrapped(&app->theme, task_stage(app),
                   (Rectangle){right.x + 16, right.y + 218, right.width - 32, 60}, 20, 28, COLOR_ACCENT);
        (void)snprintf(value, sizeof(value), "消息 %zu · 动作 %zu", app->state.message_count,
                       app->state.action_count);
        ui_text(&app->theme, value, (Vector2){right.x + 16, right.y + 288}, 15, COLOR_MUTED);
    } else {
        ui_wrapped(&app->theme, "还没有开始\n到“对话”页开始聊天。",
                   (Rectangle){right.x + 16, right.y + 70, right.width - 32, 120}, 19, 30, COLOR_MUTED);
    }

    ui_panel(events, "最近事件", &app->theme);
    for (index = 0; index < 3; index++) {
        const UiEvent *event = state_event_at(&app->state, index);
        float y = events.y + 52 + (float)index * 24;
        if (event == NULL) {
            if (index == 0) ui_text(&app->theme, "暂无事件", (Vector2){events.x + 16, y}, 16, COLOR_MUTED);
            break;
        }
        format_time(event->occurred_at, time_text);
        ui_mono(&app->theme, time_text, (Vector2){events.x + 16, y + 2}, 14, COLOR_MUTED);
        ui_text(&app->theme, event->text, (Vector2){events.x + 180, y}, 16,
                strcmp(event->kind, "system") == 0 ? COLOR_WARNING : COLOR_TEXT);
    }
}
