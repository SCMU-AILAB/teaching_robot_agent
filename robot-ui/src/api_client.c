#include "api_client.h"
#include "cJSON.h"
#include "text.h"

#include <curl/curl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define API_QUEUE_LIMIT 32
#define API_RESPONSE_LIMIT (16 * 1024 * 1024)

typedef struct ApiRequest {
    uint64_t request_id;
    ApiKind kind;
    ApiMethod method;
    int64_t task_id;
    char path[256];
    char *body;
    struct ApiRequest *next;
} ApiRequest;

typedef struct ResponseNode {
    ApiResponse *response;
    struct ResponseNode *next;
} ResponseNode;

typedef struct Buffer {
    unsigned char *data;
    size_t size;
    bool overflow;
} Buffer;

/* 摄像头与证据图片走单独通道：它们可能长时间占用连接，不能挡住停止任务等命令。 */
#define API_LANE_CONTROL 0
#define API_LANE_MEDIA 1
#define API_LANE_COUNT 2

typedef struct ApiLane {
    struct ApiClient *client;
    pthread_t thread;
    bool started;
    ApiRequest *request_head;
    ApiRequest *request_tail;
    size_t request_count;
    ApiKind active_kind;
    bool active_valid;
} ApiLane;

struct ApiClient {
    char base_url[256];
    double timeout_s;
    pthread_mutex_t mutex;
    pthread_cond_t condition;
    ApiLane lanes[API_LANE_COUNT];
    ResponseNode *response_head;
    ResponseNode *response_tail;
    size_t response_count;
    uint64_t next_id;
    bool started;
    bool stopping;
};

static int lane_for(ApiKind kind) {
    return kind == API_CAMERA || kind == API_EVIDENCE ? API_LANE_MEDIA : API_LANE_CONTROL;
}

static void free_request(ApiRequest *request) {
    if (request != NULL) {
        free(request->body);
        free(request);
    }
}

void api_client_response_free(ApiResponse *response) {
    if (response != NULL) {
        free(response->data);
        free(response);
    }
}

static size_t write_data(void *contents, size_t size, size_t count, void *user_data) {
    size_t bytes = size * count;
    Buffer *buffer = user_data;
    unsigned char *resized;
    if (bytes == 0) {
        return 0;
    }
    if (buffer->size + bytes + 1 > API_RESPONSE_LIMIT) {
        buffer->overflow = true;
        return 0;
    }
    resized = realloc(buffer->data, buffer->size + bytes + 1);
    if (resized == NULL) {
        buffer->overflow = true;
        return 0;
    }
    buffer->data = resized;
    memcpy(buffer->data + buffer->size, contents, bytes);
    buffer->size += bytes;
    buffer->data[buffer->size] = '\0';
    return bytes;
}

static size_t read_header(char *buffer, size_t size, size_t count, void *user_data) {
    size_t bytes = size * count;
    ApiResponse *response = user_data;
    if (bytes > 14 && strncasecmp(buffer, "Content-Type:", 13) == 0) {
        const char *start = buffer + 13;
        const char *end = buffer + bytes;
        while (start < end && (*start == ' ' || *start == '\t')) {
            start++;
        }
        while (end > start && (end[-1] == '\r' || end[-1] == '\n' || end[-1] == ';')) {
            end--;
        }
        if (end > start) {
            size_t length = (size_t)(end - start);
            if (length >= sizeof(response->content_type)) {
                length = sizeof(response->content_type) - 1;
            }
            memcpy(response->content_type, start, length);
            response->content_type[length] = '\0';
        }
    }
    return bytes;
}

static void parse_json_result(ApiResponse *response) {
    cJSON *root;
    const cJSON *code;
    const cJSON *message;
    if (response->data == NULL || response->data_size == 0 ||
        strstr(response->content_type, "json") == NULL) {
        return;
    }
    root = cJSON_Parse((const char *)response->data);
    if (root == NULL) {
        return;
    }
    code = cJSON_GetObjectItemCaseSensitive(root, "code");
    message = cJSON_GetObjectItemCaseSensitive(root, "message");
    if (cJSON_IsNumber(code)) {
        response->api_code = code->valueint;
    }
    if (response->api_code != 0 && cJSON_IsString(message) && message->valuestring != NULL) {
        text_copy(response->error, sizeof(response->error), message->valuestring);
    }
    cJSON_Delete(root);
}

static ApiResponse *perform_request(const ApiClient *client, const ApiRequest *request) {
    ApiResponse *response = calloc(1, sizeof(*response));
    Buffer buffer = {0};
    CURL *curl;
    CURLcode result;
    char url[512];
    struct curl_slist *headers = NULL;
    if (response == NULL) {
        return NULL;
    }
    response->request_id = request->request_id;
    response->kind = request->kind;
    response->task_id = request->task_id;
    response->api_code = 0;
    curl = curl_easy_init();
    if (curl == NULL) {
        text_copy(response->error, sizeof(response->error), "无法初始化 HTTP 客户端");
        return response;
    }
    (void)snprintf(url, sizeof(url), "%s%s", client->base_url, request->path);
    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_data);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &buffer);
    curl_easy_setopt(curl, CURLOPT_HEADERFUNCTION, read_header);
    curl_easy_setopt(curl, CURLOPT_HEADERDATA, response);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 3L);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, (long)client->timeout_s);
    curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 0L);
    curl_easy_setopt(curl, CURLOPT_USERAGENT, "robot-ui/1.0");
    if (request->method == API_POST) {
        curl_easy_setopt(curl, CURLOPT_POST, 1L);
    } else if (request->method == API_PATCH) {
        curl_easy_setopt(curl, CURLOPT_CUSTOMREQUEST, "PATCH");
    }
    if (request->body != NULL) {
        headers = curl_slist_append(headers, "Content-Type: application/json");
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, request->body);
        curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, (long)strlen(request->body));
    }
    if (headers != NULL) {
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    }
    result = curl_easy_perform(curl);
    response->curl_code = (int)result;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &response->http_status);
    if (result != CURLE_OK) {
        text_copy(response->error, sizeof(response->error),
                  buffer.overflow ? "响应过大或内存不足" : curl_easy_strerror(result));
    } else if (response->http_status < 200 || response->http_status >= 300) {
        text_copy(response->error, sizeof(response->error), "后端返回错误");
    }
    response->data = buffer.data;
    response->data_size = buffer.size;
    parse_json_result(response);
    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);
    return response;
}

static void push_response(ApiClient *client, ApiResponse *response) {
    ResponseNode *node;
    if (response == NULL) {
        return;
    }
    node = calloc(1, sizeof(*node));
    if (node == NULL) {
        api_client_response_free(response);
        return;
    }
    node->response = response;
    pthread_mutex_lock(&client->mutex);
    if (client->response_count >= API_QUEUE_LIMIT) {
        ResponseNode *oldest = client->response_head;
        if (oldest != NULL) {
            client->response_head = oldest->next;
            api_client_response_free(oldest->response);
            free(oldest);
            client->response_count--;
        }
    }
    if (client->response_tail != NULL) {
        client->response_tail->next = node;
    } else {
        client->response_head = node;
    }
    client->response_tail = node;
    client->response_count++;
    pthread_mutex_unlock(&client->mutex);
}

static void *worker_main(void *argument) {
    ApiLane *lane = argument;
    ApiClient *client = lane->client;
    for (;;) {
        ApiRequest *request;
        ApiResponse *response;
        pthread_mutex_lock(&client->mutex);
        while (lane->request_head == NULL && !client->stopping) {
            pthread_cond_wait(&client->condition, &client->mutex);
        }
        if (client->stopping) {
            pthread_mutex_unlock(&client->mutex);
            break;
        }
        request = lane->request_head;
        lane->request_head = request->next;
        if (lane->request_head == NULL) {
            lane->request_tail = NULL;
        }
        lane->request_count--;
        lane->active_kind = request->kind;
        lane->active_valid = true;
        pthread_mutex_unlock(&client->mutex);
        response = perform_request(client, request);
        push_response(client, response);
        pthread_mutex_lock(&client->mutex);
        lane->active_valid = false;
        pthread_mutex_unlock(&client->mutex);
        free_request(request);
    }
    return NULL;
}

ApiClient *api_client_create(const char *base_url, double timeout_s) {
    ApiClient *client = calloc(1, sizeof(*client));
    if (client == NULL) {
        return NULL;
    }
    text_copy(client->base_url, sizeof(client->base_url), base_url);
    client->timeout_s = timeout_s;
    client->next_id = 1;
    for (int index = 0; index < API_LANE_COUNT; index++) {
        client->lanes[index].client = client;
    }
    if (pthread_mutex_init(&client->mutex, NULL) != 0) {
        free(client);
        return NULL;
    }
    if (pthread_cond_init(&client->condition, NULL) != 0) {
        pthread_mutex_destroy(&client->mutex);
        free(client);
        return NULL;
    }
    return client;
}

bool api_client_start(ApiClient *client) {
    if (client == NULL || client->started) {
        return false;
    }
    for (int index = 0; index < API_LANE_COUNT; index++) {
        ApiLane *lane = &client->lanes[index];
        if (pthread_create(&lane->thread, NULL, worker_main, lane) != 0) {
            api_client_stop(client);
            return false;
        }
        lane->started = true;
    }
    client->started = true;
    return true;
}

bool api_client_submit(ApiClient *client, ApiKind kind, ApiMethod method,
                       const char *path, const char *body, int64_t task_id,
                       uint64_t *request_id) {
    ApiRequest *request;
    ApiLane *lane;
    bool accepted = false;
    if (client == NULL || !client->started || path == NULL || strlen(path) >= 256) {
        return false;
    }
    request = calloc(1, sizeof(*request));
    if (request == NULL) {
        return false;
    }
    request->kind = kind;
    request->method = method;
    request->task_id = task_id;
    text_copy(request->path, sizeof(request->path), path);
    if (body != NULL) {
        request->body = strdup(body);
        if (request->body == NULL) {
            free_request(request);
            return false;
        }
    }
    pthread_mutex_lock(&client->mutex);
    lane = &client->lanes[lane_for(kind)];
    if (!client->stopping && lane->request_count < API_QUEUE_LIMIT) {
        request->request_id = client->next_id++;
        if (lane->request_tail != NULL) {
            lane->request_tail->next = request;
        } else {
            lane->request_head = request;
        }
        lane->request_tail = request;
        lane->request_count++;
        if (request_id != NULL) {
            *request_id = request->request_id;
        }
        accepted = true;
        /* 两个通道共用条件变量，广播才能确保唤醒对应通道的线程 */
        pthread_cond_broadcast(&client->condition);
    }
    pthread_mutex_unlock(&client->mutex);
    if (!accepted) {
        free_request(request);
    }
    return accepted;
}

bool api_client_poll(ApiClient *client, ApiResponse **response) {
    ResponseNode *node;
    if (client == NULL || response == NULL) {
        return false;
    }
    pthread_mutex_lock(&client->mutex);
    node = client->response_head;
    if (node != NULL) {
        client->response_head = node->next;
        if (client->response_head == NULL) {
            client->response_tail = NULL;
        }
        client->response_count--;
    }
    pthread_mutex_unlock(&client->mutex);
    if (node == NULL) {
        return false;
    }
    *response = node->response;
    free(node);
    return true;
}

bool api_client_busy(const ApiClient *client, ApiKind kind) {
    bool busy = false;
    ApiRequest *request;
    const ApiLane *lane;
    if (client == NULL) {
        return false;
    }
    lane = &client->lanes[lane_for(kind)];
    pthread_mutex_lock((pthread_mutex_t *)&client->mutex);
    busy = lane->active_valid && lane->active_kind == kind;
    for (request = lane->request_head; !busy && request != NULL; request = request->next) {
        busy = request->kind == kind;
    }
    pthread_mutex_unlock((pthread_mutex_t *)&client->mutex);
    return busy;
}

void api_client_stop(ApiClient *client) {
    ApiRequest *requests[API_LANE_COUNT];
    ResponseNode *node;
    if (client == NULL) {
        return;
    }
    pthread_mutex_lock(&client->mutex);
    client->stopping = true;
    for (int index = 0; index < API_LANE_COUNT; index++) {
        ApiLane *lane = &client->lanes[index];
        requests[index] = lane->request_head;
        lane->request_head = NULL;
        lane->request_tail = NULL;
        lane->request_count = 0;
    }
    pthread_cond_broadcast(&client->condition);
    pthread_mutex_unlock(&client->mutex);
    for (int index = 0; index < API_LANE_COUNT; index++) {
        ApiRequest *request = requests[index];
        while (request != NULL) {
            ApiRequest *next = request->next;
            free_request(request);
            request = next;
        }
        if (client->lanes[index].started) {
            pthread_join(client->lanes[index].thread, NULL);
            client->lanes[index].started = false;
        }
    }
    client->started = false;
    pthread_mutex_lock(&client->mutex);
    node = client->response_head;
    client->response_head = NULL;
    client->response_tail = NULL;
    client->response_count = 0;
    pthread_mutex_unlock(&client->mutex);
    while (node != NULL) {
        ResponseNode *next = node->next;
        api_client_response_free(node->response);
        free(node);
        node = next;
    }
}

void api_client_destroy(ApiClient *client) {
    if (client != NULL) {
        api_client_stop(client);
        pthread_cond_destroy(&client->condition);
        pthread_mutex_destroy(&client->mutex);
        free(client);
    }
}
