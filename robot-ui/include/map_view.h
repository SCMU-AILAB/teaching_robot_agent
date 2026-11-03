#ifndef ROBOT_UI_MAP_VIEW_H
#define ROBOT_UI_MAP_VIEW_H

#include "raylib.h"
#include "cJSON.h"
#include "theme.h"

#include <stdbool.h>

/* 后端 GET /api/v1/map 的显示状态；纹理只在主线程创建与更新 */
typedef struct MapView {
    bool has_map;
    int width;
    int height;
    float resolution;
    float origin_x;
    float origin_y;
    float robot_x;
    float robot_y;
    float robot_yaw;
    bool has_home;
    float home_x;
    float home_y;
    Texture2D texture;
    bool has_texture;
    char error[128];
} MapView;

void map_view_init(MapView *view);
void map_view_unload(MapView *view);
/* 接收 MapResponse 的 data；格式不对返回 false 并记录原因 */
bool map_view_accept(MapView *view, const cJSON *data);
void map_view_set_error(MapView *view, const char *message);
void map_view_draw(const MapView *view, const Theme *theme, Rectangle area);

#endif
