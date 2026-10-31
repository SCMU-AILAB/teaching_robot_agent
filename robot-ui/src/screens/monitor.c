#include "screens/monitor.h"
#include "text.h"
#include "ui_common.h"

#include <stdio.h>
#include <string.h>

typedef struct TimelineItem {
    double time;
    char label[256];
    Color color;
} TimelineItem;

static void add_item(TimelineItem *items, size_t *count, double time, const char *label, Color color) {
    if (*count >= 14) return;
    items[*count].time = time;
    text_copy(items[*count].label, sizeof(items[*count].label), label);
    items[*count].color = color;
    (*count)++;
}

static void sort_items(TimelineItem *items, size_t count) {
    size_t i;
    for (i = 1; i < count; i++) {
        TimelineItem value = items[i];
        size_t j = i;
        while (j > 0 && items[j - 1].time > value.time) {
            items[j] = items[j - 1];
            j--;
        }
        items[j] = value;
    }
}

void monitor_render(App *app, Rectangle content) {
    Rectangle task = {content.x, content.y, 316, content.height};
    Rectangle timeline = {content.x + 328, content.y, 552, content.height};
    Rectangle diagnostics = {content.x + 892, content.y, 356, content.height};
    TimelineItem items[14];
    size_t count = 0;
    size_t index;
    char value[512];
    char time_text[32];
    ui_panel(task, "任务信息", &app->theme);
    if (app->state.task.task_id > 0) {
        (void)snprintf(value, sizeof(value), "#%lld", (long long)app->state.task.task_id);
        ui_mono(&app->theme, value, (Vector2){task.x + 16, task.y + 62}, 23, COLOR_ACCENT);
        ui_badge(&app->theme, (Rectangle){task.x + 142, task.y + 54, 154, 32},
                 app->state.task.status, ui_service_color(app->state.task.status));
        ui_text(&app->theme, "Created At", (Vector2){task.x + 16, task.y + 112}, 12, COLOR_MUTED);
        format_time(app->state.task.created_at, time_text);
        ui_mono(&app->theme, time_text, (Vector2){task.x + 16, task.y + 136}, 14, COLOR_TEXT);
        ui_text(&app->theme, "User Target", (Vector2){task.x + 16, task.y + 184}, 12, COLOR_MUTED);
        ui_wrapped(&app->theme, app->state.task.user_target,
                   (Rectangle){task.x + 16, task.y + 210, task.width - 32, 100}, 15, 23, COLOR_TEXT);
        (void)snprintf(value, sizeof(value), "消息 %zu / 动作 %zu / 观察 %s", app->state.message_count,
                       app->state.action_count, app->state.has_observation ? "有" : "无");
        ui_wrapped(&app->theme, value, (Rectangle){task.x + 16, task.y + 340, task.width - 32, 54},
                   13, 21, COLOR_MUTED);
        if (app->state.truncated_actions) {
            ui_text(&app->theme, "动作快照已截断", (Vector2){task.x + 16, task.y + 420}, 12, COLOR_WARNING);
        }
    } else {
        ui_text(&app->theme, "暂无任务数据", (Vector2){task.x + 16, task.y + 66}, 15, COLOR_MUTED);
    }

    ui_panel(timeline, "任务时间线", &app->theme);
    if (app->state.task.task_id > 0) {
        add_item(items, &count, app->state.task.created_at, "任务创建", COLOR_ACCENT);
    }
    for (index = 0; index < app->state.message_count && count < 7; index++) {
        const MessageRecord *message = &app->state.messages[index];
        (void)snprintf(value, sizeof(value), "%s · %s",
                       strcmp(message->role, "assistant") == 0 ? "AI 响应" : "用户输入",
                       message->status);
        add_item(items, &count, message->created_at, value,
                 strcmp(message->role, "assistant") == 0 ? COLOR_ACCENT : COLOR_TEXT);
    }
    for (index = 0; index < app->state.action_count && count < 11; index++) {
        const ActionRecord *action = &app->state.actions[index];
        (void)snprintf(value, sizeof(value), "%s · %s", action->skill_name, action->status);
        add_item(items, &count, action->ended_at > 0 ? action->ended_at :
                 action->started_at > 0 ? action->started_at : action->created_at,
                 value, ui_service_color(action->status));
    }
    if (app->state.has_observation) {
        add_item(items, &count, app->state.observation.analyzed_at, "观察结果已生成", COLOR_SUCCESS);
    }
    if (app->state.task.ended_at > 0) {
        (void)snprintf(value, sizeof(value), "任务终态 · %s", app->state.task.status);
        add_item(items, &count, app->state.task.ended_at, value, ui_service_color(app->state.task.status));
    }
    sort_items(items, count);
    if (count == 0) {
        ui_text(&app->theme, "暂无时间线事件", (Vector2){timeline.x + 18, timeline.y + 64}, 15, COLOR_MUTED);
    }
    for (index = 0; index < count && index < 11; index++) {
        float y = timeline.y + 56 + (float)index * 36;
        DrawCircle((int)timeline.x + 22, (int)y + 8, 4, items[index].color);
        if (index + 1 < count) DrawLine((int)timeline.x + 22, (int)y + 15,
                                       (int)timeline.x + 22, (int)y + 39, COLOR_BORDER);
        format_time(items[index].time, time_text);
        ui_mono(&app->theme, time_text, (Vector2){timeline.x + 38, y}, 11, COLOR_MUTED);
        ui_text(&app->theme, items[index].label, (Vector2){timeline.x + 174, y}, 13, items[index].color);
    }

    ui_panel(diagnostics, "系统诊断", &app->theme);
    ui_status_row(&app->theme, (Rectangle){diagnostics.x + 14, diagnostics.y + 58, 328, 24}, "Backend",
                  app->state.backend_connected ? "Connected" : "Offline",
                  app->state.backend_connected ? COLOR_SUCCESS : COLOR_ERROR);
    ui_status_row(&app->theme, (Rectangle){diagnostics.x + 14, diagnostics.y + 96, 328, 24}, "Agent",
                  app->state.system.agent, ui_service_color(app->state.system.agent));
    ui_status_row(&app->theme, (Rectangle){diagnostics.x + 14, diagnostics.y + 134, 328, 24}, "VLM",
                  app->state.system.vlm, ui_service_color(app->state.system.vlm));
    ui_status_row(&app->theme, (Rectangle){diagnostics.x + 14, diagnostics.y + 172, 328, 24}, "ASR / TTS",
                  app->state.system.audio_available ? "Configured" : "Not configured",
                  app->state.system.audio_available ? COLOR_SUCCESS : COLOR_WARNING);
    ui_status_row(&app->theme, (Rectangle){diagnostics.x + 14, diagnostics.y + 210, 328, 24}, "Camera",
                  app->state.system.camera_available ? "Available" : "Unavailable",
                  app->state.system.camera_available ? COLOR_SUCCESS : COLOR_ERROR);
    ui_status_row(&app->theme, (Rectangle){diagnostics.x + 14, diagnostics.y + 248, 328, 24}, "Audio",
                  app->state.system.audio_playing ? "Playing" : app->state.system.audio_recording ? "Recording" : "Idle",
                  COLOR_ACCENT);
    ui_text(&app->theme, "最近错误 / 警告", (Vector2){diagnostics.x + 16, diagnostics.y + 298}, 12, COLOR_MUTED);
    {
        const char *error = state_latest_error(&app->state);
        ui_wrapped(&app->theme, error != NULL && error[0] != '\0' ? error : "无",
                   (Rectangle){diagnostics.x + 16, diagnostics.y + 324, diagnostics.width - 32, 82},
                   13, 20, error != NULL && error[0] != '\0' ? COLOR_ERROR : COLOR_SUCCESS);
    }
    ui_text(&app->theme, "无高频遥测接口，不显示运动曲线", (Vector2){diagnostics.x + 16, diagnostics.y + 426},
            12, COLOR_MUTED);
}
