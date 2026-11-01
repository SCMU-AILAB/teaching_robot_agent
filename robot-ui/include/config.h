#ifndef ROBOT_UI_CONFIG_H
#define ROBOT_UI_CONFIG_H

#include <stdbool.h>

#define CONFIG_PATH_CAP 256

typedef struct AppConfig {
    char base_url[CONFIG_PATH_CAP];
    int window_width;
    int window_height;
    int target_fps;
    double camera_fps;
    double system_interval_s;
    double http_timeout_s;
    char font_body[CONFIG_PATH_CAP];
    char font_display[CONFIG_PATH_CAP];
    char font_mono[CONFIG_PATH_CAP];
    double idle_timeout_s;
    bool fullscreen;
} AppConfig;

void config_defaults(AppConfig *config);
bool config_load(const char *path, AppConfig *config, char *error, unsigned long error_size);
bool config_validate(const AppConfig *config, char *error, unsigned long error_size);

#endif
