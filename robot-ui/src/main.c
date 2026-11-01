#include "app.h"
#include "text.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void usage(const char *program) {
    (void)printf("用法：%s [--config PATH] [--backend URL] [--fullscreen] [--screen NAME]\n", program);
    (void)printf("           [--screenshot FILE] [--screenshot-delay SEC]\n");
    (void)printf("页面：dashboard / assistant / vision / monitor；F11 全屏，F12 截图。\n默认显示待机表情，点击或触摸后进入界面；指定 --screen 时直接进入。\n");
}

int main(int argc, char **argv) {
    App app = {0};
    const char *config_path = "config/default.json";
    char error[256];
    bool explicit_config = false;
    ScreenId initial_screen = SCREEN_DASHBOARD;
    bool screen_given = false;
    int index;
    config_defaults(&app.config);
    for (index = 1; index < argc; index++) {
        if (strcmp(argv[index], "--help") == 0 || strcmp(argv[index], "-h") == 0) {
            usage(argv[0]);
            return 0;
        }
        if (strcmp(argv[index], "--config") == 0 && index + 1 < argc) {
            config_path = argv[++index];
            explicit_config = true;
        } else if (strcmp(argv[index], "--backend") == 0 && index + 1 < argc) {
            text_copy(app.config.base_url, sizeof(app.config.base_url), argv[++index]);
        } else if (strcmp(argv[index], "--fullscreen") == 0) {
            app.config.fullscreen = true;
        } else if (strcmp(argv[index], "--screenshot") == 0 && index + 1 < argc) {
            text_copy(app.screenshot_path, sizeof(app.screenshot_path), argv[++index]);
            app.screenshot_after = 2.5;
        } else if (strcmp(argv[index], "--screenshot-delay") == 0 && index + 1 < argc) {
            app.screenshot_after = strtod(argv[++index], NULL);
        } else if (strcmp(argv[index], "--screen") == 0 && index + 1 < argc) {
            const char *name = argv[++index];
            screen_given = true;
            if (strcmp(name, "dashboard") == 0) initial_screen = SCREEN_DASHBOARD;
            else if (strcmp(name, "assistant") == 0) initial_screen = SCREEN_ASSISTANT;
            else if (strcmp(name, "vision") == 0) initial_screen = SCREEN_VISION;
            else if (strcmp(name, "monitor") == 0) initial_screen = SCREEN_MONITOR;
            else {
                (void)fprintf(stderr, "未知页面：%s\n", name);
                return 2;
            }
        } else {
            (void)fprintf(stderr, "未知参数：%s\n", argv[index]);
            usage(argv[0]);
            return 2;
        }
    }
    {
        AppConfig from_file;
        AppConfig overrides = app.config;
        if (config_load(config_path, &from_file, error, sizeof(error))) {
            app.config = from_file;
            if (strcmp(overrides.base_url, "http://127.0.0.1:8000") != 0) {
                text_copy(app.config.base_url, sizeof(app.config.base_url), overrides.base_url);
            }
            if (overrides.fullscreen) {
                app.config.fullscreen = true;
            }
        } else if (explicit_config) {
            (void)fprintf(stderr, "读取 %s 失败：%s\n", config_path, error);
            return 2;
        } else {
            (void)fprintf(stderr, "未读取配置，使用默认值：%s\n", error);
        }
    }
    {
        size_t length = strlen(app.config.base_url);
        while (length > 0 && app.config.base_url[length - 1] == '/') {
            app.config.base_url[--length] = '\0';
        }
    }
    if (!config_validate(&app.config, error, sizeof(error))) {
        (void)fprintf(stderr, "配置错误：%s\n", error);
        return 2;
    }
    if (!app_init(&app)) {
        (void)fprintf(stderr, "应用初始化失败；请检查图形环境和依赖。\n");
        app_close(&app);
        return 1;
    }
    if (initial_screen != SCREEN_DASHBOARD) {
        app_switch_screen(&app, initial_screen);
    }
    if (screen_given) {
        /* 明确指定页面时直接进入界面，便于验收和截图。 */
        app_set_standby(&app, false);
        app.standby_blend = 0.0f;
    }
    app_run(&app);
    app_close(&app);
    return 0;
}
