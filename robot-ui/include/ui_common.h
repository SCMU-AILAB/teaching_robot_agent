#ifndef ROBOT_UI_COMMON_H
#define ROBOT_UI_COMMON_H

#include "raylib.h"
#include "theme.h"
#include <stddef.h>

void ui_panel(Rectangle bounds, const char *title, const Theme *theme);
void ui_text(const Theme *theme, const char *text, Vector2 position, float size, Color color);
void ui_mono(const Theme *theme, const char *text, Vector2 position, float size, Color color);
float ui_wrapped_height(const Theme *theme, const char *text, float width, float size, float line_height);
void ui_wrapped(const Theme *theme, const char *text, Rectangle bounds, float size,
                float line_height, Color color);
bool ui_button(Rectangle bounds, const char *text, bool enabled);
void ui_badge(const Theme *theme, Rectangle bounds, const char *text, Color color);
void ui_status_row(const Theme *theme, Rectangle bounds, const char *label,
                   const char *value, Color color);
void ui_text_input(const Theme *theme, Rectangle bounds, char *text, size_t capacity,
                   bool focused);
Color ui_service_color(const char *status);
const char *speech_phase_text(int phase);
Color speech_phase_color(int phase);
/* 合同状态与技能名的中文显示 */
const char *task_status_text(const char *status);
const char *action_status_text(const char *status);
const char *skill_text(const char *skill);
const char *service_text(const char *status);

#endif
