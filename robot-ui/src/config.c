#include "config.h"
#include "cJSON.h"
#include "text.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void set_error(char *error, unsigned long size, const char *message) {
    if (error != NULL && size > 0) {
        (void)snprintf(error, size, "%s", message);
    }
}

void config_defaults(AppConfig *config) {
    memset(config, 0, sizeof(*config));
    text_copy(config->base_url, sizeof(config->base_url), "http://127.0.0.1:8000");
    config->window_width = 1280;
    config->window_height = 600;
    config->target_fps = 30;
    config->camera_fps = 3.0;
    config->system_interval_s = 1.0;
    config->http_timeout_s = 8.0;
    text_copy(config->font_body, sizeof(config->font_body),
              "assets/fonts/NotoSansSC.ttf");
    text_copy(config->font_display, sizeof(config->font_display),
              "assets/fonts/Inter-Regular.ttf");
    text_copy(config->font_mono, sizeof(config->font_mono),
              "assets/fonts/DejaVuSansMono.ttf");
    config->idle_timeout_s = 60.0;
    config->fullscreen = false;
}

bool config_validate(const AppConfig *config, char *error, unsigned long error_size) {
    size_t length = strlen(config->base_url);
    if ((strncmp(config->base_url, "http://", 7) != 0 &&
         strncmp(config->base_url, "https://", 8) != 0) || length >= sizeof(config->base_url)) {
        set_error(error, error_size, "base_url 必须是有效的 HTTP(S) 地址");
        return false;
    }
    if (config->window_width < 640 || config->window_height < 360) {
        set_error(error, error_size, "窗口尺寸过小");
        return false;
    }
    if (config->target_fps < 15 || config->target_fps > 60 ||
        config->camera_fps < 0.2 || config->camera_fps > 5.0 ||
        config->system_interval_s < 0.2 || config->system_interval_s > 10.0 ||
        config->http_timeout_s < 1.0 || config->http_timeout_s > 30.0) {
        set_error(error, error_size, "刷新率或超时配置超出允许范围");
        return false;
    }
    /* 0 表示禁用待机；其余取值限定在 5 秒到 1 小时。 */
    if (config->idle_timeout_s != 0.0 &&
        (config->idle_timeout_s < 5.0 || config->idle_timeout_s > 3600.0)) {
        set_error(error, error_size, "idle_timeout_s 必须为 0 或 5–3600 秒");
        return false;
    }
    return true;
}

static void read_string(const cJSON *root, const char *key, char *output, size_t capacity) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
    if (cJSON_IsString(item) && item->valuestring != NULL) {
        text_copy(output, capacity, item->valuestring);
    }
}

static void read_int(const cJSON *root, const char *key, int *output) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
    if (cJSON_IsNumber(item)) {
        *output = item->valueint;
    }
}

static void read_double(const cJSON *root, const char *key, double *output) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
    if (cJSON_IsNumber(item)) {
        *output = item->valuedouble;
    }
}

bool config_load(const char *path, AppConfig *config, char *error, unsigned long error_size) {
    FILE *file = fopen(path, "rb");
    char *content = NULL;
    long length;
    cJSON *root;
    bool valid;
    if (file == NULL) {
        set_error(error, error_size, "配置文件不存在");
        return false;
    }
    if (fseek(file, 0, SEEK_END) != 0 || (length = ftell(file)) < 0 ||
        fseek(file, 0, SEEK_SET) != 0 || length > 65536) {
        fclose(file);
        set_error(error, error_size, "配置文件无法读取或过大");
        return false;
    }
    content = calloc((size_t)length + 1, 1);
    if (content == NULL || fread(content, 1, (size_t)length, file) != (size_t)length) {
        free(content);
        fclose(file);
        set_error(error, error_size, "配置文件读取失败");
        return false;
    }
    fclose(file);
    root = cJSON_Parse(content);
    free(content);
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        set_error(error, error_size, "配置文件不是有效 JSON 对象");
        return false;
    }
    config_defaults(config);
    read_string(root, "base_url", config->base_url, sizeof(config->base_url));
    read_string(root, "font_body", config->font_body, sizeof(config->font_body));
    read_string(root, "font_display", config->font_display, sizeof(config->font_display));
    read_string(root, "font_mono", config->font_mono, sizeof(config->font_mono));
    read_int(root, "window_width", &config->window_width);
    read_int(root, "window_height", &config->window_height);
    read_int(root, "target_fps", &config->target_fps);
    read_double(root, "camera_fps", &config->camera_fps);
    read_double(root, "system_interval_s", &config->system_interval_s);
    read_double(root, "http_timeout_s", &config->http_timeout_s);
    read_double(root, "idle_timeout_s", &config->idle_timeout_s);
    if (cJSON_IsBool(cJSON_GetObjectItemCaseSensitive(root, "fullscreen"))) {
        config->fullscreen = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root, "fullscreen"));
    }
    cJSON_Delete(root);
    length = (long)strlen(config->base_url);
    while (length > 0 && config->base_url[length - 1] == '/') {
        config->base_url[--length] = '\0';
    }
    valid = config_validate(config, error, error_size);
    return valid;
}
