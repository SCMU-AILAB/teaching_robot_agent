#include "camera.h"
#include "text.h"
#include "ui_common.h"

#include <limits.h>
#include <string.h>

void camera_init(CameraView *camera) {
    memset(camera, 0, sizeof(*camera));
}

void camera_unload(CameraView *camera) {
    if (camera->has_live) {
        UnloadTexture(camera->live);
    }
    if (camera->has_evidence) {
        UnloadTexture(camera->evidence);
    }
    memset(camera, 0, sizeof(*camera));
}

static bool update_texture(Texture2D *target, bool *has_target, const ApiResponse *response) {
    const char *extension = strstr(response->content_type, "png") != NULL ? ".png" : ".jpg";
    Image image;
    Texture2D texture;
    if (response->data == NULL || response->data_size == 0 || response->data_size > INT_MAX) {
        return false;
    }
    image = LoadImageFromMemory(extension, response->data, (int)response->data_size);
    if (!IsImageValid(image)) {
        return false;
    }
    texture = LoadTextureFromImage(image);
    UnloadImage(image);
    if (!IsTextureValid(texture)) {
        return false;
    }
    SetTextureFilter(texture, TEXTURE_FILTER_BILINEAR);
    if (*has_target) {
        UnloadTexture(*target);
    }
    *target = texture;
    *has_target = true;
    return true;
}

bool camera_accept(CameraView *camera, const ApiResponse *response, double now) {
    bool updated = false;
    if (response->kind == API_CAMERA) {
        updated = update_texture(&camera->live, &camera->has_live, response);
        if (updated) {
            camera->live_updated_at = now;
            camera->error[0] = '\0';
        } else {
            text_copy(camera->error, sizeof(camera->error), "摄像头图像解码失败");
        }
    } else if (response->kind == API_EVIDENCE) {
        updated = update_texture(&camera->evidence, &camera->has_evidence, response);
        if (updated) {
            camera->evidence_updated_at = now;
        }
    }
    return updated;
}

void camera_draw(const CameraView *camera, Rectangle bounds, bool evidence,
                 Font font, const char *placeholder) {
    Texture2D texture = evidence ? camera->evidence : camera->live;
    bool available = evidence ? camera->has_evidence : camera->has_live;
    DrawRectangleRounded(bounds, 0.025f, 5, (Color){8, 12, 16, 255});
    if (available) {
        float scale_x = (bounds.width - 8) / (float)texture.width;
        float scale_y = (bounds.height - 8) / (float)texture.height;
        float scale = scale_x < scale_y ? scale_x : scale_y;
        Rectangle source = {0, 0, (float)texture.width, (float)texture.height};
        Rectangle target = {
            bounds.x + (bounds.width - (float)texture.width * scale) * 0.5f,
            bounds.y + (bounds.height - (float)texture.height * scale) * 0.5f,
            (float)texture.width * scale,
            (float)texture.height * scale
        };
        DrawTexturePro(texture, source, target, (Vector2){0, 0}, 0, WHITE);
    } else {
        Vector2 size = MeasureTextEx(font, placeholder, 15, 0.5f);
        DrawTextEx(font, placeholder,
                   (Vector2){bounds.x + (bounds.width - size.x) * 0.5f,
                             bounds.y + (bounds.height - size.y) * 0.5f},
                   15, 0.5f, COLOR_MUTED);
    }
    DrawRectangleRoundedLinesEx(bounds, 0.025f, 5, 1, COLOR_BORDER);
}

void camera_clear_error(CameraView *camera) {
    camera->error[0] = '\0';
}
