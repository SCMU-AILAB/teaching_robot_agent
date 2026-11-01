#include "state.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static cJSON *parse(const char *text) {
    cJSON *root = cJSON_Parse(text);
    assert(root != NULL);
    return root;
}

static void apply_message_event(UiState *state, uint64_t sequence, const char *id,
                                const char *role, const char *status, const char *error,
                                double created_at) {
    char json[1024];
    cJSON *event;
    char error_json[128];
    if (error != NULL) {
        (void)snprintf(error_json, sizeof(error_json), "{\"code\":50002,\"message\":\"%s\"}",
                       error);
    } else {
        (void)snprintf(error_json, sizeof(error_json), "null");
    }
    (void)snprintf(json, sizeof(json),
                   "{\"instance_id\":\"i\",\"sequence\":%llu,\"task_id\":7,"
                   "\"occurred_at\":%f,\"type\":\"message.updated\",\"payload\":{"
                   "\"message_id\":\"%s\",\"task_id\":7,\"role\":\"%s\",\"text\":\"t\","
                   "\"source\":\"text\",\"status\":\"%s\",\"reply_to\":null,"
                   "\"speech_action_id\":null,\"error\":%s,\"created_at\":%f}}",
                   (unsigned long long)sequence, created_at, id, role, status, error_json,
                   created_at);
    event = parse(json);
    assert(state_apply_event(state, event));
    cJSON_Delete(event);
}

/* 存满后淘汰最旧的消息；失败状态只看最近一轮。 */
static void check_eviction_and_latest_failure(void) {
    static UiState state;
    char id[32];
    uint64_t sequence;
    state_init(&state);
    apply_message_event(&state, 1, "bad", "user", "rejected", "模型服务不可用", 1.0);
    assert(state_speech_phase(&state) == SPEECH_FAILED);
    for (sequence = 2; sequence <= UI_MAX_MESSAGES + 10; sequence++) {
        (void)snprintf(id, sizeof(id), "m%llu", (unsigned long long)sequence);
        apply_message_event(&state, sequence, id, "user", "answered", NULL, (double)sequence);
    }
    assert(state.message_count == UI_MAX_MESSAGES);
    assert(state.truncated_messages);
    (void)snprintf(id, sizeof(id), "m%d", UI_MAX_MESSAGES + 10);
    assert(strcmp(state.messages[UI_MAX_MESSAGES - 1].message_id, id) == 0);
    assert(state_speech_phase(&state) == SPEECH_IDLE);
}

int main(void) {
    UiState state;
    cJSON *system = parse("{\"instance_id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"active_task_id\":7,\"simulated\":false,\"robot\":{\"connected\":true,\"moving\":false,\"x\":1.0,\"y\":2.0,\"yaw\":0.3,\"updated_at\":100.0},\"camera\":{\"available\":true,\"last_frame_at\":99.0,\"error\":null},\"audio\":{\"available\":true,\"recording\":false,\"playing\":false},\"services\":{\"agent\":\"ready\",\"vlm\":\"ready\",\"asr\":\"ready\",\"tts\":\"ready\"}}");
    cJSON *snapshot = parse("{\"instance_id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"task\":{\"task_id\":7,\"status\":\"running\",\"user_target\":\"测试\",\"created_at\":100.0,\"ended_at\":null},\"messages\":[{\"message_id\":\"m1\",\"task_id\":7,\"role\":\"user\",\"text\":\"你好\",\"source\":\"speech\",\"status\":\"processing\",\"reply_to\":null,\"speech_action_id\":null,\"error\":null,\"created_at\":101.0}],\"actions\":[],\"latest_observation\":null,\"active_recording\":null,\"truncated\":{\"messages\":false,\"actions\":false},\"event_cursor\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:4\"}");
    cJSON *event = parse("{\"instance_id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"sequence\":5,\"task_id\":7,\"occurred_at\":102.0,\"type\":\"message.updated\",\"payload\":{\"message_id\":\"m2\",\"task_id\":7,\"role\":\"assistant\",\"text\":\"你好，我在。\",\"source\":\"agent\",\"status\":\"final\",\"reply_to\":\"m1\",\"speech_action_id\":null,\"error\":null,\"created_at\":102.0}}");
    state_init(&state);
    assert(!state_apply_system(&state, system));
    assert(state.system.robot_connected);
    assert(state.system.active_task_id == 7);
    assert(state_apply_snapshot(&state, snapshot));
    assert(state.task.task_id == 7);
    assert(state.message_count == 1);
    assert(state_speech_phase(&state) == SPEECH_PROCESSING);
    assert(state_apply_event(&state, event));
    assert(state.message_count == 2);
    assert(state.last_sequence == 5);
    assert(!state_apply_event(&state, event));
    assert(state.message_count == 2);
    assert(strcmp(state.messages[1].text, "你好，我在。") == 0);
    cJSON_Delete(system);
    cJSON_Delete(snapshot);
    cJSON_Delete(event);
    check_eviction_and_latest_failure();
    (void)puts("test_state: OK");
    return 0;
}
