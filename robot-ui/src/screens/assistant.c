#include "screens/assistant.h"
#include "text.h"
#include "ui_common.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#define BUBBLE_TEXT 20.0f
#define BUBBLE_LINE 29.0f

static float message_height(const App *app, const MessageRecord *message, float width) {
    float height = ui_wrapped_height(&app->theme, message->text, width - 28, BUBBLE_TEXT, BUBBLE_LINE);
    return fmaxf(70.0f, height + 50.0f);
}

/* 气泡上方的小标签：谁说的，主动提醒单独标出 */
static const char *message_meta(const MessageRecord *message) {
    bool assistant = strcmp(message->role, "assistant") == 0;
    if (assistant) {
        return strcmp(message->source, "proactive") == 0 ? "机器人 · 主动提醒" : "机器人";
    }
    if (message->error[0] != '\0') return "我 · 这句没处理成";
    return strcmp(message->source, "speech") == 0 ? "我 · 语音" : "我";
}

static void draw_messages(App *app, Rectangle area) {
    size_t skipped = 0;
    size_t offset = (size_t)app->message_scroll;
    float bottom = area.y + area.height;
    size_t index;
    if (app->state.message_count == 0) {
        ui_wrapped(&app->theme, "按右边的大按钮说话，或者点下面的按钮问问机器人。",
                   (Rectangle){area.x + 20, area.y + 28, area.width - 40, 120}, 20, 30, COLOR_MUTED);
        return;
    }
    for (index = app->state.message_count; index > 0; index--) {
        const MessageRecord *message = &app->state.messages[index - 1];
        bool assistant = strcmp(message->role, "assistant") == 0;
        bool proactive = assistant && strcmp(message->source, "proactive") == 0;
        float max_width = area.width * 0.8f;
        float measured = MeasureTextEx(app->theme.body, message->text, BUBBLE_TEXT, 0.5f).x + 28.0f;
        float width = fminf(max_width, fmaxf(200.0f, measured));
        float height = message_height(app, message, width);
        float x = assistant ? area.x + 8 : area.x + area.width - width - 8;
        float y;
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
        if (proactive) {
            DrawRectangleRoundedLinesEx((Rectangle){x, y, width, height - 8}, 0.08f, 6, 2.0f,
                                        COLOR_WARNING);
        }
        ui_text(&app->theme, message_meta(message), (Vector2){x + 14, y + 8}, 15,
                proactive ? COLOR_WARNING : assistant ? COLOR_ACCENT : COLOR_MUTED);
        ui_wrapped(&app->theme, message->text,
                   (Rectangle){x + 14, y + 34, width - 28, height - 42}, BUBBLE_TEXT, BUBBLE_LINE,
                   COLOR_TEXT);
        bottom = y - 6;
    }
}

/* 手指上下拖动翻看历史：每拖过半条消息的高度翻一条 */
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
        if (fabsf(delta) > 40) {
            /* 手指往下拖看更早的消息 */
            app->message_scroll = fmaxf(0, fminf((float)app->state.message_count - 1,
                                                app->message_scroll + (delta > 0 ? 1 : -1)));
            app->drag_previous = mouse;
            app->message_follow = app->message_scroll == 0;
        }
    }
    if (IsMouseButtonReleased(MOUSE_BUTTON_LEFT)) {
        app->message_dragging = false;
    }
}

/* 常用说法：点一下等于孩子说了这句，触摸屏上不用打字 */
static void draw_quick_prompts(App *app, Rectangle bar, bool enabled) {
    static const char *labels[] = {"看看周围", "这能碰吗", "讲讲安全", "去探索吧"};
    static const char *texts[] = {"看看周围有什么", "这个东西能碰吗？", "讲讲家里的用电安全",
                                  "去探索一下吧"};
    const float gap = 10.0f;
    const float width = (bar.width - 24 - gap * 3) / 4.0f;
    int index;
    for (index = 0; index < 4; index++) {
        Rectangle bounds = {bar.x + 12 + index * (width + gap), bar.y + 10, width, bar.height - 20};
        if (ui_button(bounds, labels[index], enabled)) {
            text_copy(app->input_text, sizeof(app->input_text), texts[index]);
            app_send_message(app);
        }
    }
}

void assistant_render(App *app, Rectangle content) {
    Rectangle messages = {content.x, content.y, 650, 370};
    Rectangle message_area = {messages.x + 10, messages.y + 52, messages.width - 76, 308};
    Rectangle prompts = {content.x, content.y + 380, 650, content.height - 380};
    Rectangle side = {content.x + 662, content.y, content.width - 662, content.height};
    SpeechPhase phase = state_speech_phase(&app->state);
    bool active = state_task_active(&app->state);
    bool recording = app->state.has_recording &&
                     strcmp(app->state.recording.status, "recording") == 0;
    bool transcribing = app->state.has_recording &&
                        strcmp(app->state.recording.status, "transcribing") == 0;
    float half = (side.width - 32 - 12) * 0.5f;
    char value[256];
    ui_panel(messages, "对话", &app->theme);
    if (app->state.truncated_messages) {
        ui_badge(&app->theme, (Rectangle){messages.x + 470, messages.y + 8, 166, 30},
                 "只显示最近 100 条", COLOR_WARNING);
    }
    handle_scroll(app, message_area);
    if (app->message_follow) app->message_scroll = 0;
    draw_messages(app, message_area);
    if (app->state.message_count > 1) {
        if (ui_button((Rectangle){messages.x + messages.width - 60, messages.y + 54, 48, 60}, "上",
                      app->message_scroll < (float)app->state.message_count - 1)) {
            app->message_scroll = fminf((float)app->state.message_count - 1, app->message_scroll + 1);
            app->message_follow = false;
        }
        if (ui_button((Rectangle){messages.x + messages.width - 60, messages.y + 124, 48, 60}, "下",
                      app->message_scroll > 0)) {
            app->message_scroll = fmaxf(0, app->message_scroll - 1);
            app->message_follow = app->message_scroll == 0;
        }
        if (ui_button((Rectangle){messages.x + messages.width - 60, messages.y + 296, 48, 60}, "新",
                      app->message_scroll > 0)) {
            app->message_scroll = 0;
            app->message_follow = true;
        }
    }

    ui_panel(prompts, NULL, &app->theme);
    draw_quick_prompts(app, prompts,
                       active && !app->pending_message && !state_has_pending_message(&app->state) &&
                       !recording && !transcribing);

    ui_panel(side, "说话与任务", &app->theme);
    if (!active) {
        ui_wrapped(&app->theme, app->state.task.task_id > 0 ? "这次陪伴已经结束。\n按下面开始新的一次。" :
                   "还没有开始。\n按下面开始和机器人聊天。",
                   (Rectangle){side.x + 18, side.y + 64, side.width - 36, 100}, 20, 30, COLOR_MUTED);
        if (ui_button((Rectangle){side.x + 16, side.y + 176, side.width - 32, 80},
                      app->pending_create ? "正在开始…" : "开始聊天", !app->pending_create)) {
            app_start_task(app);
        }
    } else {
        ui_badge(&app->theme, (Rectangle){side.x + 16, side.y + 58, side.width - 32, 44},
                 speech_phase_text(phase), speech_phase_color(phase));
        (void)snprintf(value, sizeof(value), "第 %lld 次陪伴 · %s", (long long)app->state.task.task_id,
                       task_status_text(app->state.task.status));
        ui_text(&app->theme, value, (Vector2){side.x + 18, side.y + 112}, 17, COLOR_MUTED);
        if (ui_button((Rectangle){side.x + 16, side.y + 144, side.width - 32, 92},
                      app->pending_recording ? "请稍候…" : recording ? "说完了，按这里" : "按一下开始说话",
                      !app->pending_recording && !transcribing && app->state.system.audio_available)) {
            app_toggle_recording(app);
        }
        if (!app->state.system.audio_available) {
            ui_text(&app->theme, "后端没有可用的麦克风", (Vector2){side.x + 18, side.y + 244},
                    16, COLOR_WARNING);
        }
        if (ui_button((Rectangle){side.x + 16, side.y + 272, half, 64}, "停止",
                      !app->pending_task_update)) {
            app_cancel_task(app);
        }
        if (ui_button((Rectangle){side.x + 28 + half, side.y + 272, half, 64}, "完成",
                      !app->pending_task_update)) {
            app_complete_task(app);
        }
        ui_text(&app->theme, "“停止”不是硬件急停", (Vector2){side.x + 18, side.y + 346}, 15,
                COLOR_WARNING);
    }
    {
        const char *error = state_latest_error(&app->state);
        if (error != NULL && error[0] != '\0') {
            ui_wrapped(&app->theme, error, (Rectangle){side.x + 18, side.y + 374, side.width - 36, 76},
                       16, 24, COLOR_ERROR);
        }
    }
}
