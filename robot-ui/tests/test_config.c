#include "config.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    AppConfig config;
    char error[256];
    config_defaults(&config);
    assert(config_validate(&config, error, sizeof(error)));
    assert(config.window_width == 1280);
    assert(config.window_height == 600);
    assert(config_load("config/default.json", &config, error, sizeof(error)));
    assert(strcmp(config.base_url, "http://127.0.0.1:8000") == 0);
    assert(config.camera_fps >= 2.0 && config.camera_fps <= 5.0);
    (void)strcpy(config.base_url, "ftp://invalid");
    assert(!config_validate(&config, error, sizeof(error)));
    (void)puts("test_config: OK");
    return 0;
}
