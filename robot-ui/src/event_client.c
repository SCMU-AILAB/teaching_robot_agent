#include "event_client.h"
#include "text.h"

#include <curl/curl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define EVENT_QUEUE_LIMIT 128
#define EVENT_BUFFER_LIMIT (1024 * 1024)

typedef struct EventNode {
    EventNotification value;
    struct EventNode *next;
} EventNode;

struct EventClient {
    char base_url[256];
    int64_t task_id;
    char cursor[96];
    pthread_t thread;
    pthread_mutex_t mutex;
    pthread_cond_t condition;
    EventNode *head;
    EventNode *tail;
    size_t count;
    bool stopping;
    bool running;
    /* 收到 stream.reset 后线程退出，等界面读完快照再以新游标启动 */
    bool finished;
};

typedef struct SseParser {
    EventClient *client;
    char id[96];
    char event[64];
    char *data;
    size_t data_size;
    unsigned char *pending;
    size_t pending_size;
    bool abort_stream;
} SseParser;

void event_client_notification_free(EventNotification *notification) {
    if (notification != NULL) {
        free(notification->json);
        notification->json = NULL;
    }
}

static void clear_queue_locked(EventClient *client) {
    EventNode *node = client->head;
    while (node != NULL) {
        EventNode *next = node->next;
        event_client_notification_free(&node->value);
        free(node);
        node = next;
    }
    client->head = NULL;
    client->tail = NULL;
    client->count = 0;
}

static void push_notification(EventClient *client, EventNotificationType type,
                              const char *json, const char *message) {
    EventNode *node = calloc(1, sizeof(*node));
    if (node == NULL) {
        return;
    }
    node->value.type = type;
    node->value.task_id = client->task_id;
    if (json != NULL) {
        node->value.json = strdup(json);
        if (node->value.json == NULL) {
            free(node);
            return;
        }
    }
    text_copy(node->value.message, sizeof(node->value.message), message);
    pthread_mutex_lock(&client->mutex);
    if (client->count >= EVENT_QUEUE_LIMIT) {
        clear_queue_locked(client);
        node->value.type = EVENT_RESET;
        free(node->value.json);
        node->value.json = NULL;
        text_copy(node->value.message, sizeof(node->value.message), "事件队列溢出，重新读取快照");
    }
    if (client->tail != NULL) {
        client->tail->next = node;
    } else {
        client->head = node;
    }
    client->tail = node;
    client->count++;
    pthread_mutex_unlock(&client->mutex);
}

static bool is_stopping(EventClient *client) {
    bool stopping;
    pthread_mutex_lock(&client->mutex);
    stopping = client->stopping;
    pthread_mutex_unlock(&client->mutex);
    return stopping;
}

static void update_cursor(EventClient *client, const char *cursor) {
    pthread_mutex_lock(&client->mutex);
    text_copy(client->cursor, sizeof(client->cursor), cursor);
    pthread_mutex_unlock(&client->mutex);
}

static void dispatch_event(SseParser *parser) {
    const char *json = parser->data != NULL ? parser->data : "";
    if (parser->event[0] == '\0') {
        return;
    }
    if (parser->id[0] != '\0') {
        update_cursor(parser->client, parser->id);
    }
    if (strcmp(parser->event, "stream.reset") == 0) {
        pthread_mutex_lock(&parser->client->mutex);
        parser->client->finished = true;
        pthread_mutex_unlock(&parser->client->mutex);
        push_notification(parser->client, EVENT_RESET, json, "事件流需要重新同步");
        parser->abort_stream = true;
    } else {
        push_notification(parser->client, EVENT_RESOURCE, json, parser->event);
    }
}

static void process_line(SseParser *parser, const char *line, size_t length) {
    const char *value;
    if (length > 0 && line[length - 1] == '\r') {
        length--;
    }
    if (length == 0) {
        dispatch_event(parser);
        parser->event[0] = '\0';
        parser->id[0] = '\0';
        parser->data_size = 0;
        if (parser->data != NULL) {
            parser->data[0] = '\0';
        }
        return;
    }
    if (line[0] == ':') {
        return;
    }
    value = memchr(line, ':', length);
    if (value == NULL) {
        return;
    }
    {
        size_t field_length = (size_t)(value - line);
        value++;
        if ((size_t)(value - line) < length && *value == ' ') {
            value++;
        }
        length -= (size_t)(value - line);
        if (field_length == 2 && strncmp(line, "id", 2) == 0) {
            size_t copy = length < sizeof(parser->id) - 1 ? length : sizeof(parser->id) - 1;
            memcpy(parser->id, value, copy);
            parser->id[copy] = '\0';
        } else if (field_length == 5 && strncmp(line, "event", 5) == 0) {
            size_t copy = length < sizeof(parser->event) - 1 ? length : sizeof(parser->event) - 1;
            memcpy(parser->event, value, copy);
            parser->event[copy] = '\0';
        } else if (field_length == 4 && strncmp(line, "data", 4) == 0) {
            char *resized;
            if (parser->data_size + length + 2 > EVENT_BUFFER_LIMIT) {
                parser->abort_stream = true;
                return;
            }
            resized = realloc(parser->data, parser->data_size + length + 2);
            if (resized == NULL) {
                parser->abort_stream = true;
                return;
            }
            parser->data = resized;
            memcpy(parser->data + parser->data_size, value, length);
            parser->data_size += length;
            parser->data[parser->data_size++] = '\n';
            parser->data[parser->data_size] = '\0';
        }
    }
}

static size_t consume_sse(void *contents, size_t size, size_t count, void *user_data) {
    size_t bytes = size * count;
    SseParser *parser = user_data;
    size_t start = 0;
    size_t index;
    unsigned char *resized;
    if (is_stopping(parser->client)) {
        return 0;
    }
    if (parser->pending_size + bytes > EVENT_BUFFER_LIMIT) {
        return 0;
    }
    resized = realloc(parser->pending, parser->pending_size + bytes);
    if (resized == NULL) {
        return 0;
    }
    parser->pending = resized;
    memcpy(parser->pending + parser->pending_size, contents, bytes);
    parser->pending_size += bytes;
    for (index = 0; index < parser->pending_size; index++) {
        if (parser->pending[index] == '\n') {
            process_line(parser, (const char *)parser->pending + start, index - start);
            start = index + 1;
            if (parser->abort_stream) {
                return 0;
            }
        }
    }
    if (start > 0) {
        memmove(parser->pending, parser->pending + start, parser->pending_size - start);
        parser->pending_size -= start;
    }
    return bytes;
}

static int progress_sse(void *user_data, curl_off_t download_total, curl_off_t downloaded,
                        curl_off_t upload_total, curl_off_t uploaded) {
    EventClient *client = user_data;
    (void)download_total;
    (void)downloaded;
    (void)upload_total;
    (void)uploaded;
    return is_stopping(client) ? 1 : 0;
}

static void run_stream(EventClient *client) {
    CURL *curl = curl_easy_init();
    SseParser parser = {.client = client};
    struct curl_slist *headers = NULL;
    char url[512];
    char cursor_header[128];
    if (curl == NULL) {
        push_notification(client, EVENT_STATUS, NULL, "无法初始化事件流");
        return;
    }
    pthread_mutex_lock(&client->mutex);
    (void)snprintf(url, sizeof(url), "%s/api/v1/tasks/%lld/events", client->base_url,
                   (long long)client->task_id);
    (void)snprintf(cursor_header, sizeof(cursor_header), "Last-Event-ID: %s", client->cursor);
    pthread_mutex_unlock(&client->mutex);
    headers = curl_slist_append(headers, "Accept: text/event-stream");
    headers = curl_slist_append(headers, "Cache-Control: no-cache");
    headers = curl_slist_append(headers, cursor_header);
    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, consume_sse);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &parser);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 3L);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 0L);
    curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(curl, CURLOPT_NOPROGRESS, 0L);
    curl_easy_setopt(curl, CURLOPT_XFERINFOFUNCTION, progress_sse);
    curl_easy_setopt(curl, CURLOPT_XFERINFODATA, client);
    curl_easy_setopt(curl, CURLOPT_USERAGENT, "robot-ui/1.0");
    (void)curl_easy_perform(curl);
    free(parser.data);
    free(parser.pending);
    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);
}

static bool is_finished(EventClient *client) {
    bool finished;
    pthread_mutex_lock(&client->mutex);
    finished = client->finished;
    pthread_mutex_unlock(&client->mutex);
    return finished;
}

static void *event_main(void *argument) {
    EventClient *client = argument;
    while (!is_stopping(client)) {
        run_stream(client);
        if (is_finished(client)) {
            break;
        }
        if (!is_stopping(client)) {
            struct timespec delay;
            push_notification(client, EVENT_STATUS, NULL, "事件流已断开，正在重连");
            clock_gettime(CLOCK_REALTIME, &delay);
            delay.tv_sec += 1;
            pthread_mutex_lock(&client->mutex);
            if (!client->stopping) {
                (void)pthread_cond_timedwait(&client->condition, &client->mutex, &delay);
            }
            pthread_mutex_unlock(&client->mutex);
        }
    }
    return NULL;
}

EventClient *event_client_create(const char *base_url) {
    EventClient *client = calloc(1, sizeof(*client));
    if (client == NULL) {
        return NULL;
    }
    text_copy(client->base_url, sizeof(client->base_url), base_url);
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

bool event_client_start(EventClient *client, int64_t task_id, const char *cursor) {
    if (client == NULL || client->running || task_id <= 0 || cursor == NULL) {
        return false;
    }
    client->task_id = task_id;
    text_copy(client->cursor, sizeof(client->cursor), cursor);
    client->stopping = false;
    client->finished = false;
    if (pthread_create(&client->thread, NULL, event_main, client) != 0) {
        return false;
    }
    client->running = true;
    return true;
}

bool event_client_streaming(EventClient *client, int64_t task_id) {
    bool streaming;
    if (client == NULL) {
        return false;
    }
    pthread_mutex_lock(&client->mutex);
    streaming = client->running && !client->finished && !client->stopping &&
                client->task_id == task_id;
    pthread_mutex_unlock(&client->mutex);
    return streaming;
}

void event_client_update_cursor(EventClient *client, const char *cursor) {
    if (client != NULL && cursor != NULL) {
        update_cursor(client, cursor);
    }
}

bool event_client_poll(EventClient *client, EventNotification *notification) {
    EventNode *node;
    if (client == NULL || notification == NULL) {
        return false;
    }
    pthread_mutex_lock(&client->mutex);
    node = client->head;
    if (node != NULL) {
        client->head = node->next;
        if (client->head == NULL) {
            client->tail = NULL;
        }
        client->count--;
    }
    pthread_mutex_unlock(&client->mutex);
    if (node == NULL) {
        return false;
    }
    *notification = node->value;
    free(node);
    return true;
}

void event_client_stop(EventClient *client) {
    if (client == NULL) {
        return;
    }
    pthread_mutex_lock(&client->mutex);
    client->stopping = true;
    pthread_cond_broadcast(&client->condition);
    pthread_mutex_unlock(&client->mutex);
    if (client->running) {
        pthread_join(client->thread, NULL);
        client->running = false;
    }
    pthread_mutex_lock(&client->mutex);
    clear_queue_locked(client);
    pthread_mutex_unlock(&client->mutex);
}

void event_client_destroy(EventClient *client) {
    if (client != NULL) {
        event_client_stop(client);
        pthread_cond_destroy(&client->condition);
        pthread_mutex_destroy(&client->mutex);
        free(client);
    }
}
