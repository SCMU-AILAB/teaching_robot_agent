#include "screens/dashboard.h"
#include "text.h"
#include "ui_common.h"

#include <stdio.h>
#include <string.h>

static const char *task_stage(const App *app) {
    size_t index;
    SpeechPhase phase = state_speech_phase(&app->state);
    if (phase == SPEECH_RECORDING) return "等待用户语音";
    if (phase == SPEECH_TRANSCRIBING) return "语音识别中";
    if (phase == SPEECH_PROCESSING) return "AI 正在处理";
    if (phase == SPEECH_SPEAKING) return "正在播报回答";
    for (index = app->state.action_count; index > 0; index--) {
        const ActionRecord *action = &app->state.actions[index - 1];
        if (strcmp(action->status, "running") == 0 || strcmp(action->status, "verifying") == 0) {
            return action->skill_name;
        }
    }
    if (!app->state.snapshot_loaded) return "等待任务";
    return app->state.task.status;
}

void dashboard_render(App *app, Rectangle content) {
    Rectangle left = {content.x, content.y, 270, 316};
    Rectangle center = {content.x + 282, content.y, 580, 316};
    Rectangle right = {content.x + 874, content.y, 374, 316};
    Rectangle events = {content.x, content.y + 328, content.width, 138};
    char value[256];
    char time_text[32];
    size_t index;
    ui_panel(left, "设备与服务", &app->theme);
    ui_status_row(&app->theme, (Rectangle){left.x + 14, left.y + 58, 242, 24}, "Robot",
                  app->state.system.robot_connected ? "Connected" : "Disconnected",
                  app->state.system.robot_connected ? COLOR_SUCCESS : COLOR_ERROR);
    ui_status_row(&app->theme, (Rectangle){left.x + 14, left.y + 96, 242, 24}, "Backend",
                  app->state.backend_connected ? "Connected" : "Offline",
                  app->state.backend_connected ? COLOR_SUCCESS : COLOR_ERROR);
    ui_status_row(&app->theme, (Rectangle){left.x + 14, left.y + 134, 242, 24}, "AI Service",
                  app->state.system.agent[0] != '\0' ? app->state.system.agent : "unknown",
                  ui_service_color(app->state.system.agent));
    ui_status_row(&app->theme, (Rectangle){left.x + 14, left.y + 172, 242, 24}, "Camera",
                  app->state.system.camera_available ? "Available" : "Unavailable",
                  app->state.system.camera_available ? COLOR_SUCCESS : COLOR_ERROR);
    (void)snprintf(value, sizeof(value), "%s%s%s", app->state.system.audio_available ? "Ready" : "Off",
                   app->state.system.audio_recording ? " · REC" : "",
                   app->state.system.audio_playing ? " · PLAY" : "");
    ui_status_row(&app->theme, (Rectangle){left.x + 14, left.y + 210, 242, 24}, "Audio", value,
                  app->state.system.audio_available ? COLOR_SUCCESS : COLOR_WARNING);
    ui_status_row(&app->theme, (Rectangle){left.x + 14, left.y + 248, 242, 24}, "System",
                  !app->state.backend_connected ? "Unknown" :
                  app->state.system.runtime_blocked_reason[0] == '\0' ? "Normal" : "Blocked",
                  !app->state.backend_connected ? COLOR_WARNING :
                  app->state.system.runtime_blocked_reason[0] == '\0' ? COLOR_SUCCESS : COLOR_ERROR);

    ui_panel(center, "实时摄像头", &app->theme);
    camera_draw(&app->camera, (Rectangle){center.x + 12, center.y + 52, center.width - 24, 220},
                false, app->theme.body,
                app->state.system.camera_available ? "等待摄像头画面…" : "摄像头不可用");
    format_time(app->camera.live_updated_at > 0 ? app->camera.live_updated_at :
                app->state.system.camera_last_frame_at, time_text);
    (void)snprintf(value, sizeof(value), "LIVE · 最后更新 %s", time_text);
    ui_text(&app->theme, value, (Vector2){center.x + 14, center.y + 282}, 13, COLOR_MUTED);
    if (app->camera.error[0] != '\0') {
        ui_badge(&app->theme, (Rectangle){center.x + 402, center.y + 10, 162, 28}, "STREAM ERROR", COLOR_ERROR);
    }

    ui_panel(right, "当前任务与 AI", &app->theme);
    if (app->state.task.task_id > 0) {
        (void)snprintf(value, sizeof(value), "TASK #%lld", (long long)app->state.task.task_id);
        ui_mono(&app->theme, value, (Vector2){right.x + 16, right.y + 58}, 18, COLOR_ACCENT);
        ui_badge(&app->theme, (Rectangle){right.x + 222, right.y + 52, 132, 30},
                 app->state.task.status, ui_service_color(app->state.task.status));
        ui_text(&app->theme, "用户目标", (Vector2){right.x + 16, right.y + 102}, 13, COLOR_MUTED);
        ui_wrapped(&app->theme, app->state.task.user_target,
                   (Rectangle){right.x + 16, right.y + 126, right.width - 32, 52}, 15, 22, COLOR_TEXT);
        ui_text(&app->theme, "当前阶段", (Vector2){right.x + 16, right.y + 190}, 13, COLOR_MUTED);
        ui_wrapped(&app->theme, task_stage(app),
                   (Rectangle){right.x + 16, right.y + 214, right.width - 32, 52}, 17, 25, COLOR_ACCENT);
        (void)snprintf(value, sizeof(value), "消息 %zu · 动作 %zu", app->state.message_count,
                       app->state.action_count);
        ui_text(&app->theme, value, (Vector2){right.x + 16, right.y + 280}, 13, COLOR_MUTED);
    } else {
        ui_wrapped(&app->theme, "暂无活动任务\n在 Assistant 页面开始对话。",
                   (Rectangle){right.x + 16, right.y + 70, right.width - 32, 120}, 16, 26, COLOR_MUTED);
    }

    ui_panel(events, "最近事件", &app->theme);
    for (index = 0; index < 3; index++) {
        const UiEvent *event = state_event_at(&app->state, index);
        float y = events.y + 51 + (float)index * 27;
        if (event == NULL) {
            if (index == 0) ui_text(&app->theme, "暂无事件", (Vector2){events.x + 16, y}, 14, COLOR_MUTED);
            break;
        }
        format_time(event->occurred_at, time_text);
        ui_mono(&app->theme, time_text, (Vector2){events.x + 16, y}, 12, COLOR_MUTED);
        ui_badge(&app->theme, (Rectangle){events.x + 142, y - 3, 92, 24}, event->kind, COLOR_ACCENT);
        ui_text(&app->theme, event->text, (Vector2){events.x + 250, y}, 13,
                strcmp(event->kind, "system") == 0 ? COLOR_WARNING : COLOR_TEXT);
    }
}
