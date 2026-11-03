#include "screens/map.h"
#include "map_view.h"
#include "ui_common.h"

#include <stdio.h>

void map_render(App *app, Rectangle content) {
    Rectangle panel = {content.x, content.y, content.width - 236, content.height};
    Rectangle legend = {content.x + content.width - 226, content.y, 226, content.height};
    char value[96];
    ui_panel(panel, "地图", &app->theme);
    map_view_draw(&app->map, &app->theme,
                  (Rectangle){panel.x + 12, panel.y + 54, panel.width - 24, panel.height - 66});

    ui_panel(legend, "图例", &app->theme);
    DrawCircle((int)legend.x + 26, (int)legend.y + 72, 9, COLOR_ACCENT);
    ui_text(&app->theme, "机器人和朝向", (Vector2){legend.x + 44, legend.y + 61}, 18, COLOR_TEXT);
    DrawRing((Vector2){legend.x + 26, legend.y + 112}, 8, 12, 0, 360, 24, COLOR_SUCCESS);
    ui_text(&app->theme, "出发点", (Vector2){legend.x + 44, legend.y + 101}, 18, COLOR_TEXT);
    DrawRectangle((int)legend.x + 17, (int)legend.y + 142, 18, 18, (Color){205, 205, 210, 255});
    ui_text(&app->theme, "看过的空地", (Vector2){legend.x + 44, legend.y + 141}, 18, COLOR_TEXT);
    DrawRectangle((int)legend.x + 17, (int)legend.y + 182, 18, 18, (Color){228, 91, 101, 255});
    ui_text(&app->theme, "障碍物", (Vector2){legend.x + 44, legend.y + 181}, 18, COLOR_TEXT);
    DrawRectangle((int)legend.x + 17, (int)legend.y + 222, 18, 18, (Color){30, 30, 33, 255});
    DrawRectangleLines((int)legend.x + 17, (int)legend.y + 222, 18, 18, COLOR_BORDER);
    ui_text(&app->theme, "还没看过", (Vector2){legend.x + 44, legend.y + 221}, 18, COLOR_TEXT);
    if (app->map.has_map) {
        (void)snprintf(value, sizeof(value), "每格 %.0f 厘米", app->map.resolution * 100.0f);
        ui_text(&app->theme, value, (Vector2){legend.x + 18, legend.y + 270}, 16, COLOR_MUTED);
    }
    ui_wrapped(&app->theme, "车停下时把看到的画上去；按轮子里程推算位置，走远了会有些偏。",
               (Rectangle){legend.x + 18, legend.y + 304, legend.width - 36, 140}, 16, 25,
               COLOR_MUTED);
}
