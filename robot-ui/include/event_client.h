#ifndef ROBOT_UI_EVENT_CLIENT_H
#define ROBOT_UI_EVENT_CLIENT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum EventNotificationType {
    EVENT_RESOURCE = 0,
    EVENT_RESET,
    EVENT_STATUS
} EventNotificationType;

typedef struct EventNotification {
    EventNotificationType type;
    int64_t task_id;
    char *json;
    char message[128];
} EventNotification;

typedef struct EventClient EventClient;

EventClient *event_client_create(const char *base_url);
bool event_client_start(EventClient *client, int64_t task_id, const char *cursor);
/* 事件流线程正在推送该任务的事件（未停止、未因 stream.reset 退出）。 */
bool event_client_streaming(EventClient *client, int64_t task_id);
void event_client_update_cursor(EventClient *client, const char *cursor);
void event_client_stop(EventClient *client);
void event_client_destroy(EventClient *client);
bool event_client_poll(EventClient *client, EventNotification *notification);
void event_client_notification_free(EventNotification *notification);

#endif
