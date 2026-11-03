#include "screens/vision.h"
#include "text.h"
#include "ui_common.h"

#include <stdio.h>

void vision_render(App *app, Rectangle content) {
    Rectangle view = {content.x, content.y, 650, content.height};
    Rectangle details = {content.x + 662, content.y, content.width - 662, content.height};
    char value[256];
    char time_text[32];
    size_t index;
    float y;
    ui_panel(view, app->vision_show_evidence ? "观察时的画面" : "实时画面", &app->theme);
    if (ui_button((Rectangle){view.x + view.width - 284, view.y + 4, 132, 40}, "实时",
                  app->vision_show_evidence)) {
        app->vision_show_evidence = false;
    }
    if (ui_button((Rectangle){view.x + view.width - 144, view.y + 4, 132, 40}, "观察时",
                  !app->vision_show_evidence && app->state.has_observation &&
                  app->state.observation.frame_evidence_id[0] != '\0')) {
        app->vision_show_evidence = true;
    }
    camera_draw(&app->camera, (Rectangle){view.x + 12, view.y + 54, view.width - 24, 360},
                app->vision_show_evidence, app->theme.body,
                app->vision_show_evidence ? "这张画面已经清理了" :
                app->state.system.camera_available ? "等待摄像头画面…" : "摄像头不可用");
    format_time(app->vision_show_evidence ? app->state.observation.captured_at :
                app->camera.live_updated_at, time_text);
    (void)snprintf(value, sizeof(value), "%s · %s", app->vision_show_evidence ? "观察时" : "实时",
                   time_text);
    ui_text(&app->theme, value, (Vector2){view.x + 16, view.y + 424}, 15, COLOR_MUTED);
    if (app->camera.error[0] != '\0') {
        ui_badge(&app->theme, (Rectangle){view.x + view.width - 148, view.y + 418, 136, 30}, "画面出错",
                 COLOR_ERROR);
    }
    if (app->state.system.simulated) {
        ui_badge(&app->theme, (Rectangle){view.x + 16, view.y + 62, 112, 30}, "模拟", COLOR_WARNING);
    }

    ui_panel(details, "机器人看到了", &app->theme);
    if (!app->state.has_observation) {
        ui_wrapped(&app->theme, "还没有观察结果。\n问机器人“看看周围有什么”，它就会看一看。",
                   (Rectangle){details.x + 16, details.y + 66, details.width - 32, 140}, 19, 29, COLOR_MUTED);
        return;
    }
    if (app->state.observation.stale) {
        ui_badge(&app->theme, (Rectangle){details.x + details.width - 140, details.y + 8, 126, 30},
                 "车动过了", COLOR_WARNING);
    }
    ui_wrapped(&app->theme, app->state.observation.summary,
               (Rectangle){details.x + 16, details.y + 58, details.width - 32, 116}, 18, 27, COLOR_TEXT);
    ui_text(&app->theme, "物体和位置", (Vector2){details.x + 16, details.y + 184}, 15, COLOR_MUTED);
    y = details.y + 210;
    if (app->state.observation.object_count == 0) {
        ui_text(&app->theme, "没有认出明确的物体", (Vector2){details.x + 16, y}, 18, COLOR_MUTED);
    }
    for (index = 0; index < app->state.observation.object_count && index < 5; index++) {
        const ObservationObject *object = &app->state.observation.objects[index];
        (void)snprintf(value, sizeof(value), "%s · %s", object->label, object->position);
        ui_text(&app->theme, value, (Vector2){details.x + 16, y}, 19, COLOR_TEXT);
        y += 32;
    }
    format_time(app->state.observation.captured_at, time_text);
    (void)snprintf(value, sizeof(value), "拍摄 %s · %s", time_text,
                   app->state.observation.simulated ? "模拟感知" : "真实画面");
    ui_text(&app->theme, value, (Vector2){details.x + 16, details.y + 420}, 15, COLOR_MUTED);
}
