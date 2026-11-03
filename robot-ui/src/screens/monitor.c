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
    Rectangle task = {content.x, content.y, 254, content.height};
    Rectangle timeline = {content.x + 264, content.y, 446, content.height};
    Rectangle diagnostics = {content.x + 720, content.y, content.width - 720, content.height};
    TimelineItem items[14];
    size_t count = 0;
    size_t index;
    char value[512];
    char time_text[32];
    const float row_x = diagnostics.x + 14;
    const float row_w = diagnostics.width - 28;
    ui_panel(task, "任务", &app->theme);
    if (app->state.task.task_id > 0) {
        (void)snprintf(value, sizeof(value), "#%lld", (long long)app->state.task.task_id);
        ui_mono(&app->theme, value, (Vector2){task.x + 16, task.y + 60}, 26, COLOR_ACCENT);
        ui_badge(&app->theme, (Rectangle){task.x + task.width - 124, task.y + 56, 108, 34},
                 task_status_text(app->state.task.status), ui_service_color(app->state.task.status));
        ui_text(&app->theme, "开始时间", (Vector2){task.x + 16, task.y + 108}, 15, COLOR_MUTED);
        format_time(app->state.task.created_at, time_text);
        ui_mono(&app->theme, time_text, (Vector2){task.x + 16, task.y + 132}, 16, COLOR_TEXT);
        ui_text(&app->theme, "今天的安排", (Vector2){task.x + 16, task.y + 174}, 15, COLOR_MUTED);
        ui_wrapped(&app->theme, app->state.task.user_target,
                   (Rectangle){task.x + 16, task.y + 198, task.width - 32, 112}, 17, 26, COLOR_TEXT);
        (void)snprintf(value, sizeof(value), "消息 %zu\n动作 %zu\n观察 %s", app->state.message_count,
                       app->state.action_count, app->state.has_observation ? "有" : "无");
        ui_wrapped(&app->theme, value, (Rectangle){task.x + 16, task.y + 322, task.width - 32, 84},
                   16, 26, COLOR_MUTED);
        if (app->state.truncated_actions) {
            ui_text(&app->theme, "动作记录已截断", (Vector2){task.x + 16, task.y + 420}, 15, COLOR_WARNING);
        }
    } else {
        ui_text(&app->theme, "还没有任务", (Vector2){task.x + 16, task.y + 66}, 18, COLOR_MUTED);
    }

    ui_panel(timeline, "时间线", &app->theme);
    if (app->state.task.task_id > 0) {
        add_item(items, &count, app->state.task.created_at, "开始陪伴", COLOR_ACCENT);
    }
    for (index = 0; index < app->state.message_count && count < 7; index++) {
        const MessageRecord *message = &app->state.messages[index];
        bool assistant = strcmp(message->role, "assistant") == 0;
        (void)snprintf(value, sizeof(value), "%s",
                       !assistant ? "孩子说话" :
                       strcmp(message->source, "proactive") == 0 ? "机器人主动提醒" : "机器人回答");
        add_item(items, &count, message->created_at, value, assistant ? COLOR_ACCENT : COLOR_TEXT);
    }
    for (index = 0; index < app->state.action_count && count < 11; index++) {
        const ActionRecord *action = &app->state.actions[index];
        (void)snprintf(value, sizeof(value), "%s · %s", skill_text(action->skill_name),
                       action_status_text(action->status));
        add_item(items, &count, action->ended_at > 0 ? action->ended_at :
                 action->started_at > 0 ? action->started_at : action->created_at,
                 value, ui_service_color(action->status));
    }
    if (app->state.has_observation) {
        add_item(items, &count, app->state.observation.analyzed_at, "看完周围", COLOR_SUCCESS);
    }
    if (app->state.task.ended_at > 0) {
        (void)snprintf(value, sizeof(value), "结束 · %s", task_status_text(app->state.task.status));
        add_item(items, &count, app->state.task.ended_at, value, ui_service_color(app->state.task.status));
    }
    sort_items(items, count);
    if (count == 0) {
        ui_text(&app->theme, "暂无事件", (Vector2){timeline.x + 18, timeline.y + 64}, 18, COLOR_MUTED);
    }
    for (index = 0; index < count && index < 10; index++) {
        float y = timeline.y + 58 + (float)index * 39;
        DrawCircle((int)timeline.x + 22, (int)y + 10, 5, items[index].color);
        if (index + 1 < count && index + 1 < 10) {
            DrawLine((int)timeline.x + 22, (int)y + 17, (int)timeline.x + 22, (int)y + 42, COLOR_BORDER);
        }
        format_time(items[index].time, time_text);
        ui_mono(&app->theme, time_text, (Vector2){timeline.x + 38, y + 2}, 14, COLOR_MUTED);
        ui_text(&app->theme, items[index].label, (Vector2){timeline.x + 196, y}, 18, items[index].color);
    }

    ui_panel(diagnostics, "系统诊断", &app->theme);
    ui_status_row(&app->theme, (Rectangle){row_x, diagnostics.y + 60, row_w, 26}, "后端",
                  app->state.backend_connected ? "已连接" : "离线",
                  app->state.backend_connected ? COLOR_SUCCESS : COLOR_ERROR);
    ui_status_row(&app->theme, (Rectangle){row_x, diagnostics.y + 100, row_w, 26}, "对话模型",
                  service_text(app->state.system.agent), ui_service_color(app->state.system.agent));
    ui_status_row(&app->theme, (Rectangle){row_x, diagnostics.y + 140, row_w, 26}, "视觉模型",
                  service_text(app->state.system.vlm), ui_service_color(app->state.system.vlm));
    ui_status_row(&app->theme, (Rectangle){row_x, diagnostics.y + 180, row_w, 26}, "语音",
                  app->state.system.audio_available ? "已配置" : "未配置",
                  app->state.system.audio_available ? COLOR_SUCCESS : COLOR_WARNING);
    ui_status_row(&app->theme, (Rectangle){row_x, diagnostics.y + 220, row_w, 26}, "摄像头",
                  app->state.system.camera_available ? "可用" : "不可用",
                  app->state.system.camera_available ? COLOR_SUCCESS : COLOR_ERROR);
    ui_status_row(&app->theme, (Rectangle){row_x, diagnostics.y + 260, row_w, 26}, "声音",
                  app->state.system.audio_playing ? "播放中" :
                  app->state.system.audio_recording ? "录音中" : "空闲",
                  COLOR_ACCENT);
    ui_text(&app->theme, "最近的错误", (Vector2){diagnostics.x + 16, diagnostics.y + 304}, 15, COLOR_MUTED);
    {
        const char *error = state_latest_error(&app->state);
        ui_wrapped(&app->theme, error != NULL && error[0] != '\0' ? error : "无",
                   (Rectangle){diagnostics.x + 16, diagnostics.y + 328, diagnostics.width - 32, 120},
                   16, 24, error != NULL && error[0] != '\0' ? COLOR_ERROR : COLOR_SUCCESS);
    }
}
