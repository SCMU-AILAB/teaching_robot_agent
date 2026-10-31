#include "ui_common.h"
#include "text.h"
#include <string.h>

#define RAYGUI_IMPLEMENTATION
#include "raygui.h"

#define UI_INPUT_BUFFER_CAP 8192u

void ui_panel(Rectangle bounds, const char *title, const Theme *theme) {
    DrawRectangleRounded(bounds, 0.035f, 6, COLOR_PANEL);
    DrawRectangleRoundedLinesEx(bounds, 0.035f, 6, 1.0f, COLOR_BORDER);
    if (title != NULL && title[0] != '\0') {
        ui_text(theme, title, (Vector2){bounds.x + 14, bounds.y + 11}, 17, COLOR_TEXT);
        DrawLineEx((Vector2){bounds.x + 12, bounds.y + 42},
                   (Vector2){bounds.x + bounds.width - 12, bounds.y + 42}, 1, COLOR_BORDER);
    }
}

void ui_text(const Theme *theme, const char *text, Vector2 position, float size, Color color) {
    DrawTextEx(theme->body, text, position, size, 0.5f, color);
}

void ui_mono(const Theme *theme, const char *text, Vector2 position, float size, Color color) {
    DrawTextEx(theme->mono, text, position, size, 0.5f, color);
}

static float advance_for(Font font, int codepoint, float size) {
    int index = GetGlyphIndex(font, codepoint);
    float advance = 0.0f;
    if (index >= 0 && index < font.glyphCount) {
        advance = (float)font.glyphs[index].advanceX;
    }
    if (advance == 0) {
        advance = (float)font.baseSize * 0.5f;
    }
    return advance * size / (float)font.baseSize + 0.5f;
}

static float line_count(Font font, const char *text, float width, float size) {
    float lines = 1.0f;
    float x = 0.0f;
    int bytes = 0;
    const char *cursor = text;
    while (*cursor != '\0') {
        int codepoint = GetCodepointNext(cursor, &bytes);
        float advance;
        cursor += bytes;
        if (codepoint == '\n') {
            lines += 1.0f;
            x = 0.0f;
            continue;
        }
        advance = advance_for(font, codepoint, size);
        if (x > 0.0f && x + advance > width) {
            lines += 1.0f;
            x = 0.0f;
        }
        x += advance;
    }
    return lines;
}

float ui_wrapped_height(const Theme *theme, const char *text, float width, float size,
                        float line_height) {
    return line_count(theme->body, text, width, size) * line_height;
}

void ui_wrapped(const Theme *theme, const char *text, Rectangle bounds, float size,
                float line_height, Color color) {
    float x = bounds.x;
    float y = bounds.y;
    int bytes = 0;
    const char *cursor = text;
    while (*cursor != '\0' && y + line_height <= bounds.y + bounds.height) {
        int codepoint = GetCodepointNext(cursor, &bytes);
        float advance;
        cursor += bytes;
        if (codepoint == '\n') {
            x = bounds.x;
            y += line_height;
            continue;
        }
        advance = advance_for(theme->body, codepoint, size);
        if (x > bounds.x && x + advance > bounds.x + bounds.width) {
            x = bounds.x;
            y += line_height;
            if (y + line_height > bounds.y + bounds.height) {
                break;
            }
        }
        DrawTextCodepoint(theme->body, codepoint, (Vector2){x, y}, size, color);
        x += advance;
    }
}

bool ui_button(Rectangle bounds, const char *text, bool enabled) {
    int result;
    if (!enabled) {
        GuiSetState(STATE_DISABLED);
    }
    result = GuiButton(bounds, text);
    GuiSetState(STATE_NORMAL);
    return enabled && result != 0;
}

void ui_badge(const Theme *theme, Rectangle bounds, const char *text, Color color) {
    Color fill = color;
    fill.a = 32;
    DrawRectangleRounded(bounds, 0.25f, 5, fill);
    DrawRectangleRoundedLinesEx(bounds, 0.25f, 5, 1, color);
    ui_text(theme, text, (Vector2){bounds.x + 8, bounds.y + 4}, 12, COLOR_TEXT);
}

void ui_status_row(const Theme *theme, Rectangle bounds, const char *label,
                   const char *value, Color color) {
    DrawCircle((int)(bounds.x + 7), (int)(bounds.y + 10), 4, color);
    ui_text(theme, label, (Vector2){bounds.x + 18, bounds.y + 2}, 14, COLOR_MUTED);
    ui_text(theme, value, (Vector2){bounds.x + bounds.width * 0.55f, bounds.y + 2}, 14, color);
}

void ui_text_input(const Theme *theme, Rectangle bounds, char *text, size_t capacity,
                   bool focused) {
    int codepoint;
    DrawRectangleRounded(bounds, 0.12f, 5, COLOR_BACKGROUND);
    DrawRectangleRoundedLinesEx(bounds, 0.12f, 5, focused ? 2.0f : 1.0f,
                                focused ? COLOR_ACCENT : COLOR_BORDER);
    if (focused) {
        codepoint = GetCharPressed();
        while (codepoint > 0) {
            if (codepoint >= 32 && codepoint != 127 && utf8_length(text) < 2000) {
                (void)utf8_append(text, capacity, codepoint);
            }
            codepoint = GetCharPressed();
        }
        if (IsKeyPressed(KEY_BACKSPACE) || IsKeyPressedRepeat(KEY_BACKSPACE)) {
            (void)utf8_backspace(text);
        }
    }
    if (text[0] != '\0') {
        const char *visible = text;
        float available = bounds.width - 24;
        char clipped[UI_INPUT_BUFFER_CAP];
        if (MeasureTextEx(theme->body, text, 15, 0.5f).x <= available) {
            ui_text(theme, text, (Vector2){bounds.x + 12, bounds.y + 15}, 15, COLOR_TEXT);
        } else {
            while (*visible != '\0') {
                int bytes = 0;
                const char *next = visible;
                (void)GetCodepointNext(next, &bytes);
                next += bytes;
                if (MeasureTextEx(theme->body, next, 15, 0.5f).x <= available) {
                    visible = next;
                } else {
                    break;
                }
            }
            text_copy(clipped, sizeof(clipped), visible);
            ui_text(theme, clipped, (Vector2){bounds.x + 12, bounds.y + 15}, 15, COLOR_TEXT);
        }
    } else {
        ui_text(theme, "输入要发送给机器人的文字…",
                (Vector2){bounds.x + 12, bounds.y + 15}, 15, COLOR_MUTED);
    }
    if (focused && ((int)(GetTime() * 2.0) % 2 == 0)) {
        float width = MeasureTextEx(theme->body, text, 15, 0.5f).x;
        float caret_x = bounds.x + 12 + width;
        if (caret_x > bounds.x + bounds.width - 12) {
            caret_x = bounds.x + bounds.width - 12;
        }
        DrawLineEx((Vector2){caret_x, bounds.y + 12},
                   (Vector2){caret_x, bounds.y + bounds.height - 12}, 1, COLOR_ACCENT);
    }
}

Color ui_service_color(const char *status) {
    if (strcmp(status, "ready") == 0 || strcmp(status, "running") == 0 ||
        strcmp(status, "succeeded") == 0 || strcmp(status, "completed") == 0) {
        return COLOR_SUCCESS;
    }
    if (strcmp(status, "unavailable") == 0 || strcmp(status, "failed") == 0 ||
        strcmp(status, "timed_out") == 0) {
        return COLOR_ERROR;
    }
    return COLOR_WARNING;
}

const char *speech_phase_text(int phase) {
    static const char *labels[] = {"Idle", "Recording", "Transcribing", "Processing", "Speaking", "Failed"};
    return phase >= 0 && phase <= 5 ? labels[phase] : "Idle";
}

Color speech_phase_color(int phase) {
    if (phase == 5) {
        return COLOR_ERROR;
    }
    if (phase > 0) {
        return COLOR_ACCENT;
    }
    return COLOR_MUTED;
}
