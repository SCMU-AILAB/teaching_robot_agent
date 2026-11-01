#ifndef ROBOT_UI_CAMERA_H
#define ROBOT_UI_CAMERA_H

#include "raylib.h"
#include "api_client.h"
#include "state.h"

typedef struct CameraView {
    Texture2D live;
    Texture2D evidence;
    bool has_live;
    bool has_evidence;
    double live_updated_at;
    double evidence_updated_at;
    char error[256];
    char evidence_id[UI_ID_CAP];
} CameraView;

void camera_init(CameraView *camera);
void camera_unload(CameraView *camera);
bool camera_accept(CameraView *camera, const ApiResponse *response, double now);
void camera_draw(const CameraView *camera, Rectangle bounds, bool evidence,
                 Font font, const char *placeholder);
void camera_clear_error(CameraView *camera);

#endif
