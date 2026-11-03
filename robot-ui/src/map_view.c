#include "map_view.h"
#include "text.h"
#include "ui_common.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

/* 地图最大格数：后端 16 米 / 5 厘米 = 320 格见方 */
#define MAP_MAX_SIDE 400

void map_view_init(MapView *view) {
    memset(view, 0, sizeof(*view));
}

void map_view_unload(MapView *view) {
    if (view->has_texture) {
        UnloadTexture(view->texture);
    }
    memset(view, 0, sizeof(*view));
}

void map_view_set_error(MapView *view, const char *message) {
    text_copy(view->error, sizeof(view->error), message);
}

static bool read_number(const cJSON *object, const char *name, double *value) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, name);
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble)) {
        return false;
    }
    *value = item->valuedouble;
    return true;
}

static void set_texture(MapView *view, const unsigned char *cells, int width, int height) {
    static const Color colors[3] = {
        {30, 30, 33, 255},   /* 未知 */
        {205, 205, 210, 255}, /* 空地 */
        {228, 91, 101, 255},  /* 障碍 */
    };
    Color *pixels = malloc(sizeof(Color) * (size_t)width * (size_t)height);
    Image image;
    int row;
    int col;
    if (pixels == NULL) {
        return;
    }
    /* 第 0 行 y 最小，画在最下面 */
    for (row = 0; row < height; row++) {
        for (col = 0; col < width; col++) {
            unsigned char state = cells[(size_t)row * (size_t)width + (size_t)col];
            pixels[(size_t)(height - 1 - row) * (size_t)width + (size_t)col] =
                colors[state <= 2 ? state : 0];
        }
    }
    if (view->has_texture && view->texture.width == width && view->texture.height == height) {
        UpdateTexture(view->texture, pixels);
    } else {
        if (view->has_texture) {
            UnloadTexture(view->texture);
        }
        image = (Image){pixels, width, height, 1, PIXELFORMAT_UNCOMPRESSED_R8G8B8A8};
        view->texture = LoadTextureFromImage(image);
        SetTextureFilter(view->texture, TEXTURE_FILTER_POINT);
        view->has_texture = true;
    }
    free(pixels);
}

bool map_view_accept(MapView *view, const cJSON *data) {
    double resolution;
    double origin_x;
    double origin_y;
    double width;
    double height;
    double robot_x;
    double robot_y;
    double robot_yaw;
    double home_x = 0;
    double home_y = 0;
    const cJSON *cells = cJSON_GetObjectItemCaseSensitive(data, "cells");
    const cJSON *robot = cJSON_GetObjectItemCaseSensitive(data, "robot");
    const cJSON *home = cJSON_GetObjectItemCaseSensitive(data, "home");
    unsigned char *bytes;
    size_t count;
    if (!read_number(data, "resolution_m", &resolution) || resolution <= 0 ||
        !read_number(data, "origin_x", &origin_x) || !read_number(data, "origin_y", &origin_y) ||
        !read_number(data, "width", &width) || !read_number(data, "height", &height) ||
        width < 1 || height < 1 || width > MAP_MAX_SIDE || height > MAP_MAX_SIDE ||
        !cJSON_IsString(cells) || !cJSON_IsObject(robot) ||
        !read_number(robot, "x", &robot_x) || !read_number(robot, "y", &robot_y) ||
        !read_number(robot, "yaw", &robot_yaw)) {
        map_view_set_error(view, "地图数据格式不对");
        return false;
    }
    count = (size_t)width * (size_t)height;
    bytes = malloc(count);
    if (bytes == NULL) {
        return false;
    }
    if (base64_decode(cells->valuestring, bytes, count) != count) {
        free(bytes);
        map_view_set_error(view, "地图数据格式不对");
        return false;
    }
    set_texture(view, bytes, (int)width, (int)height);
    free(bytes);
    view->has_home = cJSON_IsObject(home) && read_number(home, "x", &home_x) &&
                     read_number(home, "y", &home_y);
    view->width = (int)width;
    view->height = (int)height;
    view->resolution = (float)resolution;
    view->origin_x = (float)origin_x;
    view->origin_y = (float)origin_y;
    view->robot_x = (float)robot_x;
    view->robot_y = (float)robot_y;
    view->robot_yaw = (float)robot_yaw;
    view->home_x = (float)home_x;
    view->home_y = (float)home_y;
    view->has_map = view->has_texture;
    view->error[0] = '\0';
    return true;
}

static Vector2 to_screen(const MapView *view, Rectangle frame, float scale, float x, float y) {
    float col = (x - view->origin_x) / view->resolution;
    float row = (y - view->origin_y) / view->resolution;
    return (Vector2){frame.x + col * scale, frame.y + ((float)view->height - row) * scale};
}

void map_view_draw(const MapView *view, const Theme *theme, Rectangle area) {
    float scale;
    Rectangle frame;
    Vector2 robot;
    Vector2 nose;
    DrawRectangleRec(area, (Color){20, 20, 22, 255});
    if (!view->has_map) {
        ui_wrapped(theme, view->error[0] != '\0' ? view->error : "正在读取地图…",
                   (Rectangle){area.x + 20, area.y + 20, area.width - 40, 80}, 19, 28, COLOR_MUTED);
        return;
    }
    scale = fminf(area.width / (float)view->width, area.height / (float)view->height);
    frame = (Rectangle){area.x + (area.width - (float)view->width * scale) * 0.5f,
                        area.y + (area.height - (float)view->height * scale) * 0.5f,
                        (float)view->width * scale, (float)view->height * scale};
    DrawTexturePro(view->texture, (Rectangle){0, 0, (float)view->width, (float)view->height},
                   frame, (Vector2){0, 0}, 0.0f, WHITE);
    if (view->has_home) {
        Vector2 home = to_screen(view, frame, scale, view->home_x, view->home_y);
        DrawRing(home, 9, 13, 0, 360, 24, COLOR_SUCCESS);
        ui_text(theme, "出发点", (Vector2){home.x + 14, home.y - 10}, 16, COLOR_SUCCESS);
    }
    robot = to_screen(view, frame, scale, view->robot_x, view->robot_y);
    /* 屏幕 y 向下：朝向的 y 分量取反 */
    nose = (Vector2){robot.x + cosf(view->robot_yaw) * 26.0f,
                     robot.y - sinf(view->robot_yaw) * 26.0f};
    DrawLineEx(robot, nose, 4, COLOR_ACCENT);
    DrawCircleV(robot, 10, COLOR_ACCENT);
    if (view->error[0] != '\0') {
        ui_text(theme, view->error, (Vector2){area.x + 12, area.y + area.height - 26}, 15,
                COLOR_WARNING);
    }
}
