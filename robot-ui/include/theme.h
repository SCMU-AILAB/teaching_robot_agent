#ifndef ROBOT_UI_THEME_H
#define ROBOT_UI_THEME_H

#include "raylib.h"
#include "config.h"

/* 配色取自 OrinX 宣传片：近黑中性底色、橙色强调、冷白文字。 */
#define COLOR_BACKGROUND (Color){14, 14, 15, 255}
#define COLOR_PANEL (Color){24, 24, 26, 255}
#define COLOR_PANEL_ALT (Color){33, 33, 36, 255}
#define COLOR_ACCENT (Color){240, 106, 48, 255}
#define COLOR_ACCENT_DIM (Color){74, 38, 24, 255}
#define COLOR_ACCENT_STRONG (Color){112, 54, 30, 255}
#define COLOR_HOVER (Color){44, 44, 48, 255}
#define COLOR_DISABLED_FILL (Color){20, 20, 22, 255}
#define COLOR_USER_BUBBLE (Color){58, 36, 28, 255}
#define COLOR_NOTICE_FILL (Color){52, 40, 24, 245}
#define COLOR_BORDER (Color){52, 52, 57, 255}
#define COLOR_SUCCESS (Color){72, 201, 142, 255}
#define COLOR_WARNING (Color){226, 176, 60, 255}
#define COLOR_ERROR (Color){228, 91, 101, 255}
#define COLOR_TEXT (Color){236, 236, 238, 255}
#define COLOR_MUTED (Color){144, 144, 150, 255}
#define COLOR_BEZEL (Color){204, 204, 208, 255}

typedef struct Theme {
    Font body;
    Font display;
    Font mono;
    bool body_owned;
    bool display_owned;
    bool mono_owned;
} Theme;

void theme_load(Theme *theme, const AppConfig *config);
void theme_unload(Theme *theme);

#endif
