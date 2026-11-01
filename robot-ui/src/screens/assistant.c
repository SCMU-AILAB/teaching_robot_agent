#include "screens/assistant.h"
#include "text.h"
#include "ui_common.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static float message_height(const App *app, const MessageRecord *message, float width) {
    float height = ui_wrapped_height(&app->theme, message->text, width - 28, 15, 22);
    return fmaxf(58.0f, height + 42.0f);
}

static void draw_messages(App *app, Rectangle area) {
    size_t shown = 0;
    size_t skipped = 0;
    size_t offset = (size_t)app->message_scroll;
    float bottom = area.y + area.height;
    size_t index;
    if (app->state.message_count == 0) {
        ui_wrapped(&app->theme, "开始任务后，可发送文字或使用机器人麦克风讲话。\n语音识别文本由后端自动进入对话，不会重复提交。",
                   (Rectangle){area.x + 20, area.y + 28, area.width - 40, 120}, 15, 25, COLOR_MUTED);
        return;
    }
    for (index = app->state.message_count; index > 0; index--) {
        const MessageRecord *message = &app->state.messages[index - 1];
        bool assistant = strcmp(message->role, "assistant") == 0;
        float max_width = area.width * 0.72f;
        float measured = MeasureTextEx(app->theme.body, message->text, 15, 0.5f).x + 28.0f;
        float width = fminf(max_width, fmaxf(250.0f, measured));
        float height = message_height(app, message, width);
        float x = assistant ? area.x + 12 : area.x + area.width - width - 12;
        float y;
        char meta[160];
        if (skipped < offset) {
            skipped++;
            continue;
        }
        y = bottom - height;
        if (y < area.y) {
            break;
        }
        DrawRectangleRounded((Rectangle){x, y, width, height - 8}, 0.08f, 6,
                             assistant ? COLOR_PANEL_ALT : COLOR_USER_BUBBLE);
        (void)snprintf(meta, sizeof(meta), "%s · %s%s%s", assistant ? "ROBOT" : "YOU",
                       message->source, message->status[0] != '\0' ? " · " : "", message->status);
        ui_text(&app->theme, meta, (Vector2){x + 12, y + 8}, 11,
                assistant ? COLOR_ACCENT : COLOR_MUTED);
        ui_wrapped(&app->theme, message->text,
                   (Rectangle){x + 12, y + 30, width - 24, height - 38}, 15, 22, COLOR_TEXT);
        bottom = y - 6;
        shown++;
        (void)shown;
    }
}

static void handle_scroll(App *app, Rectangle area) {
    Vector2 mouse = app_mouse_position(app);
    float wheel;
    if (CheckCollisionPointRec(mouse, area)) {
        wheel = GetMouseWheelMove();
        if (wheel != 0) {
            app->message_scroll = fmaxf(0, fminf((float)app->state.message_count - 1,
                                                app->message_scroll + wheel));
            app->message_follow = app->message_scroll == 0;
        }
        if (IsMouseButtonPressed(MOUSE_BUTTON_LEFT)) {
            app->message_dragging = true;
            app->drag_previous = mouse;
        }
    }
    if (app->message_dragging && IsMouseButtonDown(MOUSE_BUTTON_LEFT)) {
        float delta = mouse.y - app->drag_previous.y;
        if (fabsf(delta) > 36) {
            app->message_scroll = fmaxf(0, fminf((float)app->state.message_count - 1,
                                                app->message_scroll + (delta < 0 ? 1 : -1)));
            app->drag_previous = mouse;
            app->message_follow = app->message_scroll == 0;
        }
    }
    if (IsMouseButtonReleased(MOUSE_BUTTON_LEFT)) {
        app->message_dragging = false;
    }
}

void assistant_render(App *app, Rectangle content) {
    Rectangle messages = {content.x, content.y, 830, 374};
    Rectangle message_area = {messages.x + 12, messages.y + 50, messages.width - 24, 310};
    Rectangle side = {content.x + 842, content.y, 406, 466};
    Rectangle input = {content.x, content.y + 386, 830, 80};
    SpeechPhase phase = state_speech_phase(&app->state);
    bool active = state_task_active(&app->state);
    bool recording = app->state.has_recording &&
                     strcmp(app->state.recording.status, "recording") == 0;
    char value[256];
    ui_panel(messages, "对话", &app->theme);
    if (app->state.truncated_messages) {
        ui_badge(&app->theme, (Rectangle){messages.x + 654, messages.y + 9, 158, 28},
                 "仅显示最近 100 条", COLOR_WARNING);
    }
    handle_scroll(app, message_area);
    if (app->message_follow) app->message_scroll = 0;
    draw_messages(app, message_area);
    if (app->state.message_count > 1) {
        if (ui_button((Rectangle){messages.x + 766, messages.y + 52, 42, 40}, "UP", true)) {
            app->message_scroll = fminf((float)app->state.message_count - 1, app->message_scroll + 1);
            app->message_follow = false;
        }
        if (ui_button((Rectangle){messages.x + 766, messages.y + 98, 42, 40}, "DN", true)) {
            app->message_scroll = fmaxf(0, app->message_scroll - 1);
            app->message_follow = app->message_scroll == 0;
        }
    }

    ui_panel(input, NULL, &app->theme);
    if (IsMouseButtonPressed(MOUSE_BUTTON_LEFT)) {
        app->input_focused = CheckCollisionPointRec(app_mouse_position(app),
                                                    (Rectangle){input.x + 12, input.y + 14, 596, 52});
    }
    ui_text_input(&app->theme, (Rectangle){input.x + 12, input.y + 14, 596, 52},
                  app->input_text, sizeof(app->input_text), app->input_focused && active);
    if (ui_button((Rectangle){input.x + 620, input.y + 14, 194, 52},
                  app->pending_message ? "发送中…" : "发送", active && !app->pending_message) ||
        (app->input_focused && IsKeyPressed(KEY_ENTER) && active)) {
        app_send_message(app);
    }

    ui_panel(side, "语音与任务控制", &app->theme);
    if (!active) {
        ui_wrapped(&app->theme, app->state.task.task_id > 0 ? "当前任务已结束。开始新对话以继续。" :
                   "尚未开始任务。创建任务后即可使用文字和语音。",
                   (Rectangle){side.x + 18, side.y + 64, side.width - 36, 90}, 15, 24, COLOR_MUTED);
        if (ui_button((Rectangle){side.x + 18, side.y + 174, side.width - 36, 54},
                      app->pending_create ? "创建中…" : "开始新对话", !app->pending_create)) {
            app_start_task(app);
        }
    } else {
        ui_text(&app->theme, "VOICE PIPELINE", (Vector2){side.x + 18, side.y + 58}, 12, COLOR_MUTED);
        ui_badge(&app->theme, (Rectangle){side.x + 18, side.y + 84, 190, 36},
                 speech_phase_text(phase), speech_phase_color(phase));
        (void)snprintf(value, sizeof(value), "TASK #%lld · %s", (long long)app->state.task.task_id,
                       app->state.task.status);
        ui_text(&app->theme, value, (Vector2){side.x + 18, side.y + 136}, 14, COLOR_TEXT);
        if (ui_button((Rectangle){side.x + 18, side.y + 174, side.width - 36, 54},
                      app->pending_recording ? "请稍候…" : recording ? "结束录音并识别" : "开始语音输入",
                      !app->pending_recording && app->state.system.audio_available)) {
            app_toggle_recording(app);
        }
        if (!app->state.system.audio_available) {
            ui_text(&app->theme, "后端未配置可用麦克风 / ASR", (Vector2){side.x + 18, side.y + 238},
                    13, COLOR_WARNING);
        }
        if (ui_button((Rectangle){side.x + 18, side.y + 278, 178, 52}, "停止任务",
                      !app->pending_task_update)) {
            app_cancel_task(app);
        }
        if (ui_button((Rectangle){side.x + 210, side.y + 278, 178, 52}, "验收完成",
                      !app->pending_task_update)) {
            app_complete_task(app);
        }
        ui_text(&app->theme, "停止任务 ≠ 硬件急停", (Vector2){side.x + 18, side.y + 342}, 13, COLOR_WARNING);
    }
    {
        const char *error = state_latest_error(&app->state);
        if (error != NULL && error[0] != '\0') {
            ui_wrapped(&app->theme, error, (Rectangle){side.x + 18, side.y + 382, side.width - 36, 64},
                       13, 20, COLOR_ERROR);
        }
    }
}
