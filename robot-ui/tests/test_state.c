#include "state.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static cJSON *parse(const char *text) {
    cJSON *root = cJSON_Parse(text);
    assert(root != NULL);
    return root;
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
    (void)puts("test_state: OK");
    return 0;
}
