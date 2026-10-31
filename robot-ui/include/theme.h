#ifndef ROBOT_UI_THEME_H
#define ROBOT_UI_THEME_H

#include "raylib.h"
#include "config.h"

#define COLOR_BACKGROUND (Color){16, 20, 25, 255}
#define COLOR_PANEL (Color){25, 31, 39, 255}
#define COLOR_PANEL_ALT (Color){30, 38, 47, 255}
#define COLOR_ACCENT (Color){54, 185, 214, 255}
#define COLOR_BORDER (Color){38, 52, 64, 255}
#define COLOR_SUCCESS (Color){53, 201, 138, 255}
#define COLOR_WARNING (Color){233, 162, 59, 255}
#define COLOR_ERROR (Color){228, 91, 101, 255}
#define COLOR_TEXT (Color){230, 237, 242, 255}
#define COLOR_MUTED (Color){143, 157, 169, 255}

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
