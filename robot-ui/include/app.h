#ifndef ROBOT_UI_APP_H
#define ROBOT_UI_APP_H

#include "api_client.h"
#include "camera.h"
#include "config.h"
#include "event_client.h"
#include "map_view.h"
#include "router.h"
#include "state.h"
#include "theme.h"

typedef struct App {
    AppConfig config;
    Theme theme;
    Router router;
    ApiClient *api;
    EventClient *events;
    CameraView camera;
    MapView map;
    double last_map_request;
    UiState state;
    bool running;
    bool settings_open;
    bool standby;
    float standby_blend;
    double last_activity;
    bool backend_failure_logged;
    bool input_focused;
    char input_text[UI_TEXT_CAP];
    bool pending_create;
    bool pending_message;
    bool pending_task_update;
    bool pending_recording;
    bool pending_home;
    bool snapshot_pending;
    bool evidence_pending;
    double evidence_retry_at;
    double last_system_request;
    double last_camera_request;
    double last_instance_check;
    char notice[UI_ERROR_CAP];
    double notice_until;
    bool vision_show_evidence;
    float message_scroll;
    bool message_follow;
    bool message_dragging;
    Vector2 drag_previous;
    char screenshot_path[512];
    double screenshot_after;
} App;

bool app_init(App *app);
void app_run(App *app);
void app_close(App *app);
void app_switch_screen(App *app, ScreenId screen);
void app_set_standby(App *app, bool standby);
Vector2 app_mouse_position(const App *app);
void app_notice(App *app, const char *message);
void app_start_task(App *app);
void app_send_message(App *app);
void app_toggle_recording(App *app);
void app_cancel_task(App *app);
void app_complete_task(App *app);
/* 出发点改为车现在的位置（PUT /tasks/{id}/home） */
void app_set_home(App *app);
void app_request_snapshot(App *app);

#endif
