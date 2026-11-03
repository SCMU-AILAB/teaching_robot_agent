#ifndef ROBOT_UI_API_CLIENT_H
#define ROBOT_UI_API_CLIENT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum ApiMethod {
    API_GET = 0,
    API_POST,
    API_PATCH
} ApiMethod;

typedef enum ApiKind {
    API_SYSTEM = 0,
    API_SNAPSHOT,
    API_TASK_CREATE,
    API_TASK_UPDATE,
    API_MESSAGE,
    API_RECORDING_START,
    API_RECORDING_UPDATE,
    API_CAMERA,
    API_EVIDENCE,
    API_MAP
} ApiKind;

typedef struct ApiResponse {
    uint64_t request_id;
    ApiKind kind;
    int64_t task_id;
    long http_status;
    int api_code;
    int curl_code;
    unsigned char *data;
    size_t data_size;
    char content_type[64];
    char error[256];
} ApiResponse;

typedef struct ApiClient ApiClient;

ApiClient *api_client_create(const char *base_url, double timeout_s);
bool api_client_start(ApiClient *client);
void api_client_stop(ApiClient *client);
void api_client_destroy(ApiClient *client);
bool api_client_submit(ApiClient *client, ApiKind kind, ApiMethod method,
                       const char *path, const char *body, int64_t task_id,
                       uint64_t *request_id);
bool api_client_poll(ApiClient *client, ApiResponse **response);
void api_client_response_free(ApiResponse *response);
bool api_client_busy(const ApiClient *client, ApiKind kind);

#endif
