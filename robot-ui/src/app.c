#include "app.h"
#include "face.h"
#include "screens/assistant.h"
#include "screens/dashboard.h"
#include "screens/monitor.h"
#include "screens/vision.h"
#include "text.h"
#include "ui_common.h"
#include "cJSON.h"
#include "raygui.h"

#include <curl/curl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static uint64_t command_sequence = 0;

static void make_request_id(char output[128]) {
    command_sequence++;
    (void)snprintf(output, 128, "robot-ui-%ld-%lld-%llu", (long)getpid(),
                   (long long)time(NULL), (unsigned long long)command_sequence);
}

static cJSON *parse_envelope(const ApiResponse *response) {
    if (response->data == NULL || response->data_size == 0) {
        return NULL;
    }
    return cJSON_Parse((const char *)response->data);
}

static bool response_ok(const ApiResponse *response) {
    return response->curl_code == 0 && response->http_status >= 200 &&
           response->http_status < 300 && response->api_code == 0;
}

void app_notice(App *app, const char *message) {
    text_copy(app->notice, sizeof(app->notice), message);
    app->notice_until = GetTime() + 5.0;
}

Vector2 app_mouse_position(const App *app) {
    (void)app;
    return GetMousePosition();
}

static void request_system(App *app) {
    if (!api_client_busy(app->api, API_SYSTEM)) {
        (void)api_client_submit(app->api, API_SYSTEM, API_GET, "/api/v1/system", NULL, 0, NULL);
        app->last_system_request = GetTime();
    }
}

void app_request_snapshot(App *app) {
    char path[128];
    if (app->state.task.task_id <= 0) {
        if (app->state.system.active_task_id > 0) {
            app->state.task.task_id = app->state.system.active_task_id;
        } else {
            return;
        }
    }
    if (app->snapshot_pending || api_client_busy(app->api, API_SNAPSHOT)) {
        return;
    }
    (void)snprintf(path, sizeof(path), "/api/v1/tasks/%lld",
                   (long long)app->state.task.task_id);
    app->snapshot_pending = api_client_submit(app->api, API_SNAPSHOT, API_GET, path,
                                              NULL, app->state.task.task_id, NULL);
}

static bool submit_json(App *app, ApiKind kind, ApiMethod method, const char *path,
                        cJSON *body, int64_t task_id) {
    char *encoded = cJSON_PrintUnformatted(body);
    bool accepted;
    if (encoded == NULL) {
        app_notice(app, "无法编码请求");
        return false;
    }
    accepted = api_client_submit(app->api, kind, method, path, encoded, task_id, NULL);
    free(encoded);
    if (!accepted) {
        app_notice(app, "网络队列繁忙，请稍后重试");
    }
    return accepted;
}

void app_start_task(App *app) {
    cJSON *body;
    char request_id[128];
    if (app->pending_create || state_task_active(&app->state)) {
        return;
    }
    make_request_id(request_id);
    body = cJSON_CreateObject();
    cJSON_AddStringToObject(body, "client_request_id", request_id);
    cJSON_AddStringToObject(body, "user_target", "语音或文字对话");
    app->pending_create = submit_json(app, API_TASK_CREATE, API_POST, "/api/v1/tasks", body, 0);
    cJSON_Delete(body);
}

void app_send_message(App *app) {
    cJSON *body;
    char request_id[128];
    char path[160];
    if (app->pending_message || !state_task_active(&app->state) ||
        app->input_text[0] == '\0' || state_has_pending_message(&app->state) ||
        (app->state.has_recording &&
         (strcmp(app->state.recording.status, "recording") == 0 ||
          strcmp(app->state.recording.status, "transcribing") == 0))) {
        return;
    }
    make_request_id(request_id);
    (void)snprintf(path, sizeof(path), "/api/v1/tasks/%lld/messages",
                   (long long)app->state.task.task_id);
    body = cJSON_CreateObject();
    cJSON_AddStringToObject(body, "client_request_id", request_id);
    cJSON_AddStringToObject(body, "text", app->input_text);
    app->pending_message = submit_json(app, API_MESSAGE, API_POST, path, body,
                                       app->state.task.task_id);
    cJSON_Delete(body);
}

void app_toggle_recording(App *app) {
    cJSON *body;
    char request_id[128];
    char path[192];
    if (app->pending_recording || !state_task_active(&app->state) ||
        (app->state.has_recording &&
         strcmp(app->state.recording.status, "transcribing") == 0)) {
        return;
    }
    if (app->state.has_recording &&
        strcmp(app->state.recording.status, "recording") == 0) {
        (void)snprintf(path, sizeof(path), "/api/v1/tasks/%lld/recordings/%s",
                       (long long)app->state.task.task_id, app->state.recording.recording_id);
        body = cJSON_CreateObject();
        cJSON_AddStringToObject(body, "status", "transcribing");
        app->pending_recording = submit_json(app, API_RECORDING_UPDATE, API_PATCH, path, body,
                                             app->state.task.task_id);
    } else {
        make_request_id(request_id);
        (void)snprintf(path, sizeof(path), "/api/v1/tasks/%lld/recordings",
                       (long long)app->state.task.task_id);
        body = cJSON_CreateObject();
        cJSON_AddStringToObject(body, "client_request_id", request_id);
        cJSON_AddNumberToObject(body, "max_duration_s", 15);
        app->pending_recording = submit_json(app, API_RECORDING_START, API_POST, path, body,
                                             app->state.task.task_id);
    }
    cJSON_Delete(body);
}

static void update_task(App *app, const char *status) {
    cJSON *body;
    char path[128];
    if (app->pending_task_update || !state_task_active(&app->state)) {
        return;
    }
    (void)snprintf(path, sizeof(path), "/api/v1/tasks/%lld",
                   (long long)app->state.task.task_id);
    body = cJSON_CreateObject();
    cJSON_AddStringToObject(body, "status", status);
    app->pending_task_update = submit_json(app, API_TASK_UPDATE, API_PATCH, path, body,
                                           app->state.task.task_id);
    cJSON_Delete(body);
}

void app_cancel_task(App *app) {
    update_task(app, "cancelling");
}

void app_complete_task(App *app) {
    update_task(app, "completed");
}

void app_switch_screen(App *app, ScreenId screen) {
    router_go(&app->router, screen);
    app->input_focused = false;
}

static void handle_system(App *app, const ApiResponse *response) {
    cJSON *root;
    const cJSON *data;
    bool was_connected = app->state.backend_connected;
    if (!response_ok(response)) {
        app->state.backend_connected = false;
        if (!app->backend_failure_logged) {
            state_add_event(&app->state, "system", "后端连接不可用，正在重试", (double)time(NULL));
            app->backend_failure_logged = true;
        }
        return;
    }
    root = parse_envelope(response);
    data = cJSON_GetObjectItemCaseSensitive(root, "data");
    if (cJSON_IsObject(data)) {
        bool changed = state_apply_system(&app->state, data);
        if (!was_connected) {
            state_add_event(&app->state, "system", "后端连接已恢复", (double)time(NULL));
        }
        app->backend_failure_logged = false;
        if (changed) {
            event_client_stop(app->events);
            state_clear_task(&app->state);
            camera_unload(&app->camera);
            camera_init(&app->camera);
            app_notice(app, "后端进程已重启，旧任务状态已丢弃");
        }
        if (app->state.system.active_task_id > 0 &&
            (app->state.task.task_id != app->state.system.active_task_id ||
             !app->state.snapshot_loaded)) {
            app_request_snapshot(app);
        }
    }
    cJSON_Delete(root);
}

static void handle_snapshot(App *app, const ApiResponse *response) {
    cJSON *root = parse_envelope(response);
    const cJSON *data = cJSON_GetObjectItemCaseSensitive(root, "data");
    app->snapshot_pending = false;
    if (response_ok(response) && cJSON_IsObject(data)) {
        event_client_stop(app->events);
        state_apply_snapshot(&app->state, data);
        if (!event_client_start(app->events, app->state.task.task_id, app->state.event_cursor)) {
            app_notice(app, "无法启动任务事件流");
        }
        app->message_follow = true;
    } else {
        if (response->api_code == 40401 && response->task_id == app->state.task.task_id) {
            /* 任务已被后端清理：丢弃本地任务，事件流已在 stream.reset 后退出 */
            event_client_stop(app->events);
            state_clear_task(&app->state);
            app->state.system.active_task_id = 0;
        }
        app_notice(app, response->error[0] != '\0' ? response->error : "任务快照读取失败");
    }
    cJSON_Delete(root);
}

static void handle_command(App *app, const ApiResponse *response) {
    cJSON *root;
    const cJSON *data;
    switch (response->kind) {
        case API_TASK_CREATE: app->pending_create = false; break;
        case API_MESSAGE: app->pending_message = false; break;
        case API_TASK_UPDATE: app->pending_task_update = false; break;
        case API_RECORDING_START:
        case API_RECORDING_UPDATE: app->pending_recording = false; break;
        default: break;
    }
    if (!response_ok(response)) {
        app_notice(app, response->error[0] != '\0' ? response->error : "请求失败");
        return;
    }
    root = parse_envelope(response);
    data = cJSON_GetObjectItemCaseSensitive(root, "data");
    if (response->kind == API_TASK_CREATE && cJSON_IsObject(data)) {
        const cJSON *task_id = cJSON_GetObjectItemCaseSensitive(data, "task_id");
        if (cJSON_IsNumber(task_id)) {
            app->state.task.task_id = (int64_t)task_id->valuedouble;
            app->state.system.active_task_id = app->state.task.task_id;
        }
    }
    if (response->kind == API_MESSAGE) {
        app->input_text[0] = '\0';
        app->message_follow = true;
    }
    if (!event_client_streaming(app->events, app->state.task.task_id)) {
        app_request_snapshot(app);
    }
    cJSON_Delete(root);
}

static void process_responses(App *app) {
    ApiResponse *response;
    while (api_client_poll(app->api, &response)) {
        if (response->kind == API_SYSTEM) {
            handle_system(app, response);
        } else if (response->kind == API_SNAPSHOT) {
            handle_snapshot(app, response);
        } else if (response->kind == API_CAMERA || response->kind == API_EVIDENCE) {
            if (response->kind == API_EVIDENCE) {
                app->evidence_pending = false;
                if (!response_ok(response) && response->api_code != 40401) {
                    /* 临时失败：清掉已登记的编号，3 秒后重试；已清理的证据不再重试 */
                    app->camera.evidence_id[0] = '\0';
                    app->evidence_retry_at = GetTime() + 3.0;
                }
            }
            if (response_ok(response)) {
                (void)camera_accept(&app->camera, response, GetTime());
            } else if (response->kind == API_CAMERA) {
                text_copy(app->camera.error, sizeof(app->camera.error),
                          response->error[0] != '\0' ? response->error : "摄像头连接失败");
            }
        } else {
            handle_command(app, response);
        }
        api_client_response_free(response);
    }
}

static void process_events(App *app) {
    EventNotification notification;
    while (event_client_poll(app->events, &notification)) {
        if (notification.type == EVENT_RESET) {
            /* 事件流线程已退出；快照这次读不到时，系统轮询会按未加载状态继续重试 */
            app->state.snapshot_loaded = false;
            app_request_snapshot(app);
        } else if (notification.type == EVENT_RESOURCE && notification.json != NULL) {
            cJSON *root = cJSON_Parse(notification.json);
            const cJSON *data = cJSON_GetObjectItemCaseSensitive(root, "data");
            const cJSON *type = cJSON_GetObjectItemCaseSensitive(data, "type");
            bool is_message = cJSON_IsString(type) && strcmp(type->valuestring, "message.updated") == 0;
            if (cJSON_IsObject(data) && state_apply_event(&app->state, data)) {
                event_client_update_cursor(app->events, app->state.event_cursor);
                if (is_message) {
                    app->message_follow = true;
                }
            }
            cJSON_Delete(root);
        }
        event_client_notification_free(&notification);
    }
}

static void schedule_requests(App *app) {
    double now = GetTime();
    char path[192];
    if (now - app->last_system_request >= app->config.system_interval_s) {
        request_system(app);
    }
    if (!app->standby && app->state.backend_connected && app->state.system.camera_available &&
        (app->router.current == SCREEN_DASHBOARD || app->router.current == SCREEN_VISION) &&
        now - app->last_camera_request >= 1.0 / app->config.camera_fps &&
        !api_client_busy(app->api, API_CAMERA)) {
        (void)api_client_submit(app->api, API_CAMERA, API_GET,
                                "/api/v1/camera/snapshot?max_width=960", NULL, 0, NULL);
        app->last_camera_request = now;
    }
    if (app->state.has_observation && app->state.observation.frame_evidence_id[0] != '\0' &&
        strcmp(app->camera.evidence_id, app->state.observation.frame_evidence_id) != 0 &&
        !app->evidence_pending && now >= app->evidence_retry_at &&
        !api_client_busy(app->api, API_EVIDENCE)) {
        (void)snprintf(path, sizeof(path), "/api/v1/evidence/%s",
                       app->state.observation.frame_evidence_id);
        app->evidence_pending = api_client_submit(app->api, API_EVIDENCE, API_GET, path,
                                                  NULL, app->state.observation.task_id, NULL);
        if (app->evidence_pending) {
            text_copy(app->camera.evidence_id, sizeof(app->camera.evidence_id),
                      app->state.observation.frame_evidence_id);
            if (app->camera.has_evidence) {
                UnloadTexture(app->camera.evidence);
                app->camera.has_evidence = false;
            }
        }
    }
}

void app_set_standby(App *app, bool standby) {
    if (app->standby == standby) {
        return;
    }
    app->standby = standby;
    app->last_activity = GetTime();
    if (standby) {
        /* 进入待机时收起设置抽屉并取消输入焦点，唤醒后回到干净状态。 */
        app->settings_open = false;
        app->input_focused = false;
    }
}

/* 用户是否在本帧操作过屏幕：点击、触摸、滚轮或按键。
 * 不能调用 GetKeyPressed/GetCharPressed：它们会从队列取走按键，输入框就收不到字。 */
static bool user_touched(void) {
    int key;
    if (IsMouseButtonPressed(MOUSE_BUTTON_LEFT) || IsMouseButtonReleased(MOUSE_BUTTON_LEFT) ||
        IsMouseButtonDown(MOUSE_BUTTON_LEFT) || GetMouseWheelMove() != 0.0f) {
        return true;
    }
    for (key = KEY_SPACE; key <= KEY_KB_MENU; key++) {
        if (IsKeyDown(key)) {
            return true;
        }
    }
    return false;
}

/* 语音链路在工作、设置打开或输入框有内容时，不应进入待机。 */
static bool keeps_awake(const App *app) {
    SpeechPhase phase = state_speech_phase(&app->state);
    /* 失败只是提示，不是进行中的工作，不应让屏幕一直亮着 */
    return (phase != SPEECH_IDLE && phase != SPEECH_FAILED) || app->settings_open ||
           app->pending_create || app->pending_message || app->pending_recording ||
           (app->input_focused && app->input_text[0] != '\0');
}

static void update_standby(App *app, bool touched) {
    double now = GetTime();
    float target = app->standby ? 1.0f : 0.0f;
    float step = GetFrameTime() / 0.35f;
    if (app->config.idle_timeout_s <= 0.0) {
        app_set_standby(app, false);
        target = 0.0f;
    } else if (!app->standby) {
        if (touched || keeps_awake(app)) {
            app->last_activity = now;
        } else if (now - app->last_activity >= app->config.idle_timeout_s) {
            app_set_standby(app, true);
            target = 1.0f;
        }
    }
    if (app->standby_blend < target) {
        app->standby_blend = fminf(target, app->standby_blend + step);
    } else if (app->standby_blend > target) {
        app->standby_blend = fmaxf(target, app->standby_blend - step);
    }
}

/* 待机画面：只显示宣传片里的机器人表情，没有任何控件。 */
static void draw_standby(const App *app) {
    face_draw((Rectangle){240, 50, 800, 500}, state_speech_phase(&app->state),
              app->state.backend_connected, GetTime(), false, app->standby_blend);
}

static void draw_top_bar(App *app) {
    DrawRectangle(0, 0, 1280, 58, COLOR_PANEL);
    DrawLine(0, 57, 1280, 57, COLOR_BORDER);
    face_draw((Rectangle){14, 7, 64, 44}, state_speech_phase(&app->state),
              app->state.backend_connected, GetTime(), true, 1.0f);
    ui_text(&app->theme, "ROBOT OS", (Vector2){92, 16}, 23, COLOR_TEXT);
    DrawRectangle(240, 21, 2, 16, COLOR_ACCENT);
    ui_text(&app->theme, router_title(app->router.current), (Vector2){254, 19}, 16, COLOR_MUTED);
    ui_badge(&app->theme, (Rectangle){590, 13, 158, 32},
             app->state.system.robot_connected ? "ROBOT · ONLINE" : "ROBOT · OFFLINE",
             app->state.system.robot_connected ? COLOR_SUCCESS : COLOR_ERROR);
    ui_badge(&app->theme, (Rectangle){760, 13, 150, 32},
             app->state.backend_connected ? "NETWORK · OK" : "NETWORK · LOST",
             app->state.backend_connected ? COLOR_SUCCESS : COLOR_ERROR);
    ui_badge(&app->theme, (Rectangle){922, 13, 168, 32},
             strcmp(app->state.system.agent, "ready") == 0 ? "AI · READY" : "AI · NOT READY",
             ui_service_color(app->state.system.agent));
    if (ui_button((Rectangle){1110, 8, 86, 42}, app->settings_open ? "关闭" : "设置", true)) {
        app->settings_open = !app->settings_open;
    }
    if (ui_button((Rectangle){1204, 8, 58, 42}, "全屏", true)) {
        ToggleFullscreen();
    }
}

static bool draw_nav_button(App *app, Rectangle bounds, const char *label, bool active) {
    Vector2 mouse = app_mouse_position(app);
    bool hovered = CheckCollisionPointRec(mouse, bounds);
    Color fill = active ? COLOR_ACCENT_DIM :
                 hovered ? COLOR_HOVER : COLOR_PANEL_ALT;
    Color border = active ? COLOR_ACCENT : COLOR_BORDER;
    Color text = active ? COLOR_ACCENT : hovered ? COLOR_TEXT : COLOR_MUTED;
    Vector2 size;
    DrawRectangleRounded(bounds, 0.15f, 5, fill);
    DrawRectangleRoundedLinesEx(bounds, 0.15f, 5, active ? 1.5f : 1.0f, border);
    size = MeasureTextEx(app->theme.body, label, 16, 0.5f);
    ui_text(&app->theme, label,
            (Vector2){bounds.x + (bounds.width - size.x) * 0.5f,
                      bounds.y + (bounds.height - size.y) * 0.5f},
            16, text);
    if (active) {
        DrawRectangle((int)bounds.x + 24, 594, (int)bounds.width - 48, 3, COLOR_ACCENT);
    }
    return hovered && IsMouseButtonReleased(MOUSE_BUTTON_LEFT) && !active;
}

static void draw_bottom_nav(App *app) {
    static const char *labels[] = {"Dashboard", "Assistant", "Vision", "Monitor"};
    int index;
    DrawRectangle(0, 540, 1280, 60, COLOR_PANEL);
    DrawLine(0, 540, 1280, 540, COLOR_BORDER);
    for (index = 0; index < SCREEN_COUNT; index++) {
        Rectangle bounds = {324.0f + index * 162.0f, 546, 150, 50};
        if (draw_nav_button(app, bounds, labels[index], app->router.current == (ScreenId)index)) {
            app_switch_screen(app, (ScreenId)index);
        }
    }
}

static void draw_settings(App *app) {
    Rectangle bounds = {362, 142, 556, 300};
    char value[384];
    if (!app->settings_open) {
        return;
    }
    DrawRectangle(0, 0, 1280, 600, (Color){0, 0, 0, 150});
    ui_panel(bounds, "终端设置", &app->theme);
    (void)snprintf(value, sizeof(value), "后端：%s", app->config.base_url);
    ui_wrapped(&app->theme, value, (Rectangle){382, 198, 516, 50}, 15, 23, COLOR_TEXT);
    (void)snprintf(value, sizeof(value), "渲染：1280 × 600 · %d FPS · Camera %.1f fps",
                   app->config.target_fps, app->config.camera_fps);
    ui_text(&app->theme, value, (Vector2){382, 258}, 14, COLOR_MUTED);
    ui_text(&app->theme, "快捷键：F11 全屏 · 1–4 切换页面", (Vector2){382, 292}, 14, COLOR_MUTED);
    if (ui_button((Rectangle){382, 346, 236, 54}, "切换全屏模式", true)) {
        ToggleFullscreen();
    }
    if (ui_button((Rectangle){634, 346, 244, 54}, "关闭设置", true)) {
        app->settings_open = false;
    }
}

static void draw_frame(App *app) {
    float scale_x = (float)GetScreenWidth() / 1280.0f;
    float scale_y = (float)GetScreenHeight() / 600.0f;
    float scale = scale_x < scale_y ? scale_x : scale_y;
    Vector2 offset = {(GetScreenWidth() - 1280 * scale) * 0.5f,
                      (GetScreenHeight() - 600 * scale) * 0.5f};
    Camera2D view = {.offset = offset, .target = {0, 0}, .rotation = 0, .zoom = scale};
    Rectangle content = {16, 66, 1248, 466};
    SetMouseScale(1.0f / scale, 1.0f / scale);
    SetMouseOffset(-(int)offset.x, -(int)offset.y);
    BeginDrawing();
    ClearBackground(COLOR_BACKGROUND);
    BeginMode2D(view);
    if (app->standby) {
        /* 待机期间不绘制任何控件，避免点击穿透到按钮。 */
        draw_standby(app);
        EndMode2D();
        EndDrawing();
        return;
    }
    draw_top_bar(app);
    switch (app->router.current) {
        case SCREEN_DASHBOARD: dashboard_render(app, content); break;
        case SCREEN_ASSISTANT: assistant_render(app, content); break;
        case SCREEN_VISION: vision_render(app, content); break;
        case SCREEN_MONITOR: monitor_render(app, content); break;
        default: break;
    }
    draw_bottom_nav(app);
    if (app->notice[0] != '\0' && GetTime() < app->notice_until) {
        Rectangle notice = {230, 488, 820, 42};
        DrawRectangleRounded(notice, 0.15f, 5, COLOR_NOTICE_FILL);
        DrawRectangleRoundedLinesEx(notice, 0.15f, 5, 1, COLOR_WARNING);
        ui_text(&app->theme, app->notice, (Vector2){246, 500}, 14, COLOR_TEXT);
    }
    draw_settings(app);
    if (app->standby_blend > 0.0f) {
        /* 刚唤醒：待机黑场淡出，界面渐显。 */
        DrawRectangle(0, 0, 1280, 600, Fade(COLOR_BACKGROUND, app->standby_blend));
    }
    if (app->router.transition_remaining > 0.0) {
        unsigned char alpha = (unsigned char)fmin(160.0, app->router.transition_remaining / 0.18 * 160.0);
        DrawRectangle(0, 58, 1280, 484, (Color){14, 14, 15, alpha});
    }
    EndMode2D();
    EndDrawing();
}

bool app_init(App *app) {
    AppConfig config = app->config;
    char screenshot_path[sizeof(app->screenshot_path)];
    double screenshot_after = app->screenshot_after;
    text_copy(screenshot_path, sizeof(screenshot_path), app->screenshot_path);
    memset(app, 0, sizeof(*app));
    app->config = config;
    text_copy(app->screenshot_path, sizeof(app->screenshot_path), screenshot_path);
    app->screenshot_after = screenshot_after;
    app->message_follow = true;
    app->last_system_request = -100.0;
    app->last_camera_request = -100.0;
    state_init(&app->state);
    router_init(&app->router);
    camera_init(&app->camera);
    if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) {
        return false;
    }
    SetConfigFlags(FLAG_WINDOW_RESIZABLE | FLAG_VSYNC_HINT);
    InitWindow(app->config.window_width, app->config.window_height,
               "Robot OS · Intelligent Interaction Terminal");
    if (!IsWindowReady()) {
        return false;
    }
    SetTargetFPS(app->config.target_fps);
    SetExitKey(KEY_NULL);
    if (app->config.fullscreen) {
        ToggleFullscreen();
    }
    theme_load(&app->theme, &app->config);
    GuiSetFont(app->theme.body);
    GuiSetStyle(DEFAULT, TEXT_SIZE, 15);
    GuiSetStyle(BUTTON, BASE_COLOR_NORMAL, ColorToInt(COLOR_PANEL_ALT));
    GuiSetStyle(BUTTON, BASE_COLOR_FOCUSED, ColorToInt(COLOR_ACCENT_DIM));
    GuiSetStyle(BUTTON, BASE_COLOR_PRESSED, ColorToInt(COLOR_ACCENT_STRONG));
    GuiSetStyle(BUTTON, BASE_COLOR_DISABLED, ColorToInt(COLOR_DISABLED_FILL));
    GuiSetStyle(BUTTON, TEXT_COLOR_NORMAL, ColorToInt(COLOR_TEXT));
    GuiSetStyle(BUTTON, TEXT_COLOR_FOCUSED, ColorToInt(COLOR_ACCENT));
    GuiSetStyle(BUTTON, TEXT_COLOR_PRESSED, ColorToInt(COLOR_TEXT));
    GuiSetStyle(BUTTON, TEXT_COLOR_DISABLED, ColorToInt(COLOR_MUTED));
    GuiSetStyle(BUTTON, BORDER_COLOR_NORMAL, ColorToInt(COLOR_BORDER));
    GuiSetStyle(BUTTON, BORDER_COLOR_FOCUSED, ColorToInt(COLOR_ACCENT));
    GuiSetStyle(BUTTON, BORDER_COLOR_DISABLED, ColorToInt(COLOR_BORDER));
    GuiSetStyle(BUTTON, BORDER_WIDTH, 1);
    app->api = api_client_create(app->config.base_url, app->config.http_timeout_s);
    app->events = event_client_create(app->config.base_url);
    if (app->api == NULL || app->events == NULL || !api_client_start(app->api)) {
        return false;
    }
    {
        int monitor_width = GetMonitorWidth(GetCurrentMonitor());
        int monitor_height = GetMonitorHeight(GetCurrentMonitor());
        if (monitor_width > 0 && monitor_height > 0 &&
            (GetScreenWidth() > monitor_width || GetScreenHeight() > monitor_height)) {
            int target_width = monitor_width < app->config.window_width ? monitor_width : app->config.window_width;
            int target_height = (monitor_height - 56) < app->config.window_height ? monitor_height - 56 : app->config.window_height;
            SetWindowSize(target_width, target_height);
            SetWindowPosition(0, 28);
        }
    }
    app->running = true;
    /* 默认以待机画面启动；idle_timeout_s 为 0 时不使用待机。 */
    app->standby = app->config.idle_timeout_s > 0.0;
    app->standby_blend = app->standby ? 1.0f : 0.0f;
    app->last_activity = GetTime();
    request_system(app);
    TraceLog(LOG_INFO, "[robot_ui] UI ready backend=%s", app->config.base_url);
    return true;
}

void app_run(App *app) {
    while (app->running && !WindowShouldClose()) {
        bool touched = user_touched();
        router_update(&app->router, GetFrameTime());
        if (IsKeyPressed(KEY_F11)) {
            ToggleFullscreen();
        }
        if (!app->standby) {
            if (IsKeyPressed(KEY_ONE)) app_switch_screen(app, SCREEN_DASHBOARD);
            if (IsKeyPressed(KEY_TWO)) app_switch_screen(app, SCREEN_ASSISTANT);
            if (IsKeyPressed(KEY_THREE)) app_switch_screen(app, SCREEN_VISION);
            if (IsKeyPressed(KEY_FOUR)) app_switch_screen(app, SCREEN_MONITOR);
            if (IsKeyPressed(KEY_ESCAPE)) app->settings_open = false;
        }
        if (IsKeyPressed(KEY_F12)) {
            TakeScreenshot("robot_ui_capture.png");
            app_notice(app, "截图已保存为 robot_ui_capture.png");
        }
        process_responses(app);
        process_events(app);
        schedule_requests(app);
        update_standby(app, touched);
        draw_frame(app);
        /* 唤醒放在绘制之后：触发唤醒的那次点击不会被刚出现的按钮接收。 */
        if (app->standby && touched && !IsMouseButtonDown(MOUSE_BUTTON_LEFT)) {
            app_set_standby(app, false);
        }
        if (app->screenshot_path[0] != '\0' && GetTime() >= app->screenshot_after) {
            TakeScreenshot(app->screenshot_path);
            app->running = false;
        }
    }
}

void app_close(App *app) {
    app->running = false;
    event_client_destroy(app->events);
    api_client_destroy(app->api);
    app->events = NULL;
    app->api = NULL;
    camera_unload(&app->camera);
    if (IsWindowReady()) {
        theme_unload(&app->theme);
        CloseWindow();
    }
    curl_global_cleanup();
}
