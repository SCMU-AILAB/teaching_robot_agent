#include "screens/vision.h"
#include "text.h"
#include "ui_common.h"

#include <stdio.h>

void vision_render(App *app, Rectangle content) {
    Rectangle view = {content.x, content.y, 842, content.height};
    Rectangle details = {content.x + 854, content.y, 394, content.height};
    char value[256];
    char time_text[32];
    size_t index;
    float y;
    ui_panel(view, app->vision_show_evidence ? "观察证据图像" : "实时摄像头", &app->theme);
    if (ui_button((Rectangle){view.x + 536, view.y + 7, 138, 36}, "实时画面",
                  !app->vision_show_evidence)) {
        app->vision_show_evidence = false;
    }
    if (ui_button((Rectangle){view.x + 686, view.y + 7, 140, 36}, "观察证据",
                  app->state.has_observation && app->state.observation.frame_evidence_id[0] != '\0')) {
        app->vision_show_evidence = true;
    }
    camera_draw(&app->camera, (Rectangle){view.x + 12, view.y + 52, view.width - 24, 370},
                app->vision_show_evidence, app->theme.body,
                app->vision_show_evidence ? "证据图像不可用或已清理" :
                app->state.system.camera_available ? "等待摄像头画面…" : "摄像头不可用");
    format_time(app->vision_show_evidence ? app->state.observation.captured_at :
                app->camera.live_updated_at, time_text);
    (void)snprintf(value, sizeof(value), "%s · %s", app->vision_show_evidence ? "EVIDENCE" : "LIVE",
                   time_text);
    ui_text(&app->theme, value, (Vector2){view.x + 16, view.y + 434}, 13, COLOR_MUTED);
    if (app->camera.error[0] != '\0') {
        ui_badge(&app->theme, (Rectangle){view.x + 660, view.y + 430, 166, 28}, "STREAM ERROR", COLOR_ERROR);
        ui_wrapped(&app->theme, app->camera.error,
                   (Rectangle){view.x + 16, view.y + 452, view.width - 32, 18}, 12, 16, COLOR_ERROR);
    }
    if (app->state.system.simulated) {
        ui_badge(&app->theme, (Rectangle){view.x + 16, view.y + 12, 112, 28}, "SIMULATION", COLOR_WARNING);
    }

    ui_panel(details, "最近观察", &app->theme);
    if (!app->state.has_observation) {
        ui_wrapped(&app->theme, "暂无观察结果。\n机器人会在任务中按需发起观察，前端不能主动伪造结果。",
                   (Rectangle){details.x + 16, details.y + 66, details.width - 32, 120}, 15, 24, COLOR_MUTED);
        return;
    }
    if (app->state.observation.stale) {
        ui_badge(&app->theme, (Rectangle){details.x + 250, details.y + 10, 124, 28}, "STALE · 已过期", COLOR_WARNING);
    }
    if (app->state.observation.simulated) {
        ui_badge(&app->theme, (Rectangle){details.x + 16, details.y + 54, 112, 28}, "SIMULATION", COLOR_WARNING);
    }
    ui_text(&app->theme, "摘要", (Vector2){details.x + 16, details.y + 96}, 13, COLOR_MUTED);
    ui_wrapped(&app->theme, app->state.observation.summary,
               (Rectangle){details.x + 16, details.y + 120, details.width - 32, 82}, 15, 22, COLOR_TEXT);
    ui_text(&app->theme, "物体与相对方位", (Vector2){details.x + 16, details.y + 214}, 13, COLOR_MUTED);
    y = details.y + 240;
    if (app->state.observation.object_count == 0) {
        ui_text(&app->theme, "无检测框数据 · 不绘制目标框", (Vector2){details.x + 16, y}, 13, COLOR_MUTED);
        y += 28;
    }
    for (index = 0; index < app->state.observation.object_count && index < 3; index++) {
        const ObservationObject *object = &app->state.observation.objects[index];
        (void)snprintf(value, sizeof(value), "%s · %s", object->label, object->position);
        ui_text(&app->theme, value, (Vector2){details.x + 16, y}, 14, COLOR_TEXT);
        y += 28;
    }
    format_time(app->state.observation.captured_at, time_text);
    (void)snprintf(value, sizeof(value), "采集：%s", time_text);
    ui_text(&app->theme, value, (Vector2){details.x + 16, details.y + 354}, 12, COLOR_MUTED);
    format_time(app->state.observation.analyzed_at, time_text);
    (void)snprintf(value, sizeof(value), "分析：%s · TASK #%lld", time_text,
                   (long long)app->state.observation.task_id);
    ui_text(&app->theme, value, (Vector2){details.x + 16, details.y + 380}, 12, COLOR_MUTED);
    (void)snprintf(value, sizeof(value), "来源：%s", app->state.observation.simulated ? "模拟感知" : "真实 VLM");
    ui_text(&app->theme, value, (Vector2){details.x + 16, details.y + 414}, 13, COLOR_ACCENT);
}
