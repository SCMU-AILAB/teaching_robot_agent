#include "state.h"
#include "text.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *json_string(const cJSON *object, const char *key) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
    return cJSON_IsString(item) && item->valuestring != NULL ? item->valuestring : "";
}

static double json_number(const cJSON *object, const char *key) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
    return cJSON_IsNumber(item) ? item->valuedouble : 0.0;
}

static int64_t json_integer(const cJSON *object, const char *key) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
    return cJSON_IsNumber(item) ? (int64_t)item->valuedouble : 0;
}

static bool json_boolean(const cJSON *object, const char *key) {
    return cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(object, key));
}

static void copy_json_string(char *output, size_t capacity, const cJSON *object,
                             const char *key) {
    text_copy(output, capacity, json_string(object, key));
}

static void apply_task(TaskRecord *task, const cJSON *data) {
    task->task_id = json_integer(data, "task_id");
    copy_json_string(task->status, sizeof(task->status), data, "status");
    copy_json_string(task->user_target, sizeof(task->user_target), data, "user_target");
    task->created_at = json_number(data, "created_at");
    task->ended_at = json_number(data, "ended_at");
}

static void apply_message(MessageRecord *message, const cJSON *data) {
    copy_json_string(message->message_id, sizeof(message->message_id), data, "message_id");
    message->task_id = json_integer(data, "task_id");
    copy_json_string(message->role, sizeof(message->role), data, "role");
    copy_json_string(message->text, sizeof(message->text), data, "text");
    copy_json_string(message->source, sizeof(message->source), data, "source");
    copy_json_string(message->status, sizeof(message->status), data, "status");
    copy_json_string(message->reply_to, sizeof(message->reply_to), data, "reply_to");
    message->speech_action_id = json_integer(data, "speech_action_id");
    message->created_at = json_number(data, "created_at");
    copy_json_string(message->error, sizeof(message->error),
                     cJSON_GetObjectItemCaseSensitive(data, "error"), "message");
}

static void apply_action(ActionRecord *action, const cJSON *data) {
    action->action_id = json_integer(data, "action_id");
    copy_json_string(action->skill_name, sizeof(action->skill_name), data, "skill_name");
    copy_json_string(action->status, sizeof(action->status), data, "status");
    action->created_at = json_number(data, "created_at");
    action->started_at = json_number(data, "started_at");
    action->ended_at = json_number(data, "ended_at");
    copy_json_string(action->summary, sizeof(action->summary), data, "summary");
    copy_json_string(action->failure, sizeof(action->failure), data, "failure");
}

static void apply_observation(ObservationRecord *observation, const cJSON *data) {
    const cJSON *objects;
    const cJSON *item;
    observation->object_count = 0;
    copy_json_string(observation->observation_id, sizeof(observation->observation_id),
                     data, "observation_id");
    observation->task_id = json_integer(data, "task_id");
    copy_json_string(observation->question, sizeof(observation->question), data, "question");
    copy_json_string(observation->summary, sizeof(observation->summary), data, "summary");
    observation->captured_at = json_number(data, "captured_at");
    observation->analyzed_at = json_number(data, "analyzed_at");
    observation->stale = json_boolean(data, "stale");
    observation->simulated = json_boolean(data, "simulated");
    copy_json_string(observation->frame_evidence_id, sizeof(observation->frame_evidence_id),
                     data, "frame_evidence_id");
    objects = cJSON_GetObjectItemCaseSensitive(data, "objects");
    cJSON_ArrayForEach(item, objects) {
        ObservationObject *object;
        if (!cJSON_IsObject(item) || observation->object_count >= UI_MAX_OBJECTS) {
            continue;
        }
        object = &observation->objects[observation->object_count++];
        copy_json_string(object->label, sizeof(object->label), item, "label");
        copy_json_string(object->position, sizeof(object->position), item, "position");
    }
}

static void apply_recording(RecordingRecord *recording, const cJSON *data) {
    copy_json_string(recording->recording_id, sizeof(recording->recording_id), data,
                     "recording_id");
    recording->task_id = json_integer(data, "task_id");
    copy_json_string(recording->status, sizeof(recording->status), data, "status");
    recording->started_at = json_number(data, "started_at");
    recording->ended_at = json_number(data, "ended_at");
    copy_json_string(recording->transcript, sizeof(recording->transcript), data, "transcript");
    copy_json_string(recording->message_id, sizeof(recording->message_id), data, "message_id");
    copy_json_string(recording->error, sizeof(recording->error),
                     cJSON_GetObjectItemCaseSensitive(data, "error"), "message");
}

void state_init(UiState *state) {
    memset(state, 0, sizeof(*state));
}

void state_clear_task(UiState *state) {
    memset(&state->task, 0, sizeof(state->task));
    memset(state->messages, 0, sizeof(state->messages));
    memset(state->actions, 0, sizeof(state->actions));
    memset(&state->observation, 0, sizeof(state->observation));
    memset(&state->recording, 0, sizeof(state->recording));
    state->message_count = 0;
    state->action_count = 0;
    state->has_observation = false;
    state->has_recording = false;
    state->snapshot_loaded = false;
    state->event_cursor[0] = '\0';
    state->last_sequence = 0;
    state->truncated_messages = false;
    state->truncated_actions = false;
}

bool state_apply_system(UiState *state, const cJSON *data) {
    const cJSON *robot = cJSON_GetObjectItemCaseSensitive(data, "robot");
    const cJSON *camera = cJSON_GetObjectItemCaseSensitive(data, "camera");
    const cJSON *audio = cJSON_GetObjectItemCaseSensitive(data, "audio");
    const cJSON *services = cJSON_GetObjectItemCaseSensitive(data, "services");
    const char *instance = json_string(data, "instance_id");
    bool changed = state->system.instance_id[0] != '\0' &&
                   strcmp(state->system.instance_id, instance) != 0;
    copy_json_string(state->system.instance_id, sizeof(state->system.instance_id), data,
                     "instance_id");
    state->system.active_task_id = json_integer(data, "active_task_id");
    state->system.simulated = json_boolean(data, "simulated");
    copy_json_string(state->system.runtime_blocked_reason,
                     sizeof(state->system.runtime_blocked_reason), data,
                     "runtime_blocked_reason");
    state->system.robot_connected = json_boolean(robot, "connected");
    state->system.robot_moving = json_boolean(robot, "moving");
    state->system.robot_x = json_number(robot, "x");
    state->system.robot_y = json_number(robot, "y");
    state->system.robot_yaw = json_number(robot, "yaw");
    state->system.robot_updated_at = json_number(robot, "updated_at");
    state->system.camera_available = json_boolean(camera, "available");
    state->system.camera_last_frame_at = json_number(camera, "last_frame_at");
    copy_json_string(state->system.camera_error, sizeof(state->system.camera_error), camera,
                     "error");
    state->system.audio_available = json_boolean(audio, "available");
    state->system.audio_recording = json_boolean(audio, "recording");
    state->system.audio_playing = json_boolean(audio, "playing");
    copy_json_string(state->system.agent, sizeof(state->system.agent), services, "agent");
    copy_json_string(state->system.vlm, sizeof(state->system.vlm), services, "vlm");
    copy_json_string(state->system.asr, sizeof(state->system.asr), services, "asr");
    copy_json_string(state->system.tts, sizeof(state->system.tts), services, "tts");
    state->backend_connected = true;
    return changed;
}

/* 追加一条消息；满额时淘汰最旧的一条，保证最新记录总能显示。 */
static MessageRecord *append_message(UiState *state) {
    if (state->message_count == UI_MAX_MESSAGES) {
        memmove(&state->messages[0], &state->messages[1],
                sizeof(state->messages[0]) * (UI_MAX_MESSAGES - 1));
        state->message_count--;
        state->truncated_messages = true;
    }
    return &state->messages[state->message_count++];
}

static ActionRecord *append_action(UiState *state) {
    if (state->action_count == UI_MAX_ACTIONS) {
        memmove(&state->actions[0], &state->actions[1],
                sizeof(state->actions[0]) * (UI_MAX_ACTIONS - 1));
        state->action_count--;
        state->truncated_actions = true;
    }
    return &state->actions[state->action_count++];
}

bool state_apply_snapshot(UiState *state, const cJSON *data) {
    const cJSON *messages;
    const cJSON *actions;
    const cJSON *item;
    const cJSON *truncated;
    state_clear_task(state);
    copy_json_string(state->system.instance_id, sizeof(state->system.instance_id), data,
                     "instance_id");
    apply_task(&state->task, cJSON_GetObjectItemCaseSensitive(data, "task"));
    messages = cJSON_GetObjectItemCaseSensitive(data, "messages");
    cJSON_ArrayForEach(item, messages) {
        if (cJSON_IsObject(item)) {
            apply_message(append_message(state), item);
        }
    }
    actions = cJSON_GetObjectItemCaseSensitive(data, "actions");
    cJSON_ArrayForEach(item, actions) {
        if (cJSON_IsObject(item)) {
            apply_action(append_action(state), item);
        }
    }
    item = cJSON_GetObjectItemCaseSensitive(data, "latest_observation");
    if (cJSON_IsObject(item)) {
        apply_observation(&state->observation, item);
        state->has_observation = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(data, "active_recording");
    if (cJSON_IsObject(item)) {
        apply_recording(&state->recording, item);
        state->has_recording = true;
    }
    truncated = cJSON_GetObjectItemCaseSensitive(data, "truncated");
    state->truncated_messages = state->truncated_messages || json_boolean(truncated, "messages");
    state->truncated_actions = state->truncated_actions || json_boolean(truncated, "actions");
    copy_json_string(state->event_cursor, sizeof(state->event_cursor), data, "event_cursor");
    {
        const char *colon = strrchr(state->event_cursor, ':');
        state->last_sequence = colon != NULL ? (uint64_t)strtoull(colon + 1, NULL, 10) : 0;
    }
    state->snapshot_loaded = true;
    return true;
}

static void upsert_message(UiState *state, const cJSON *payload) {
    const char *id = json_string(payload, "message_id");
    size_t index;
    for (index = 0; index < state->message_count; index++) {
        if (strcmp(state->messages[index].message_id, id) == 0) {
            apply_message(&state->messages[index], payload);
            return;
        }
    }
    apply_message(append_message(state), payload);
}

static void upsert_action(UiState *state, const cJSON *payload) {
    int64_t id = json_integer(payload, "action_id");
    size_t index;
    for (index = 0; index < state->action_count; index++) {
        if (state->actions[index].action_id == id) {
            apply_action(&state->actions[index], payload);
            return;
        }
    }
    apply_action(append_action(state), payload);
}

void state_add_event(UiState *state, const char *kind, const char *text, double occurred_at) {
    size_t index;
    UiEvent *event;
    if (state->event_count < UI_MAX_EVENTS) {
        index = (state->event_head + state->event_count) % UI_MAX_EVENTS;
        state->event_count++;
    } else {
        index = state->event_head;
        state->event_head = (state->event_head + 1) % UI_MAX_EVENTS;
    }
    event = &state->events[index];
    event->occurred_at = occurred_at;
    text_copy(event->kind, sizeof(event->kind), kind);
    text_copy(event->text, sizeof(event->text), text);
}

bool state_apply_event(UiState *state, const cJSON *data) {
    uint64_t sequence = (uint64_t)json_integer(data, "sequence");
    const char *type = json_string(data, "type");
    const cJSON *payload = cJSON_GetObjectItemCaseSensitive(data, "payload");
    double occurred_at = json_number(data, "occurred_at");
    char description[512];
    if (!cJSON_IsObject(payload) || sequence <= state->last_sequence) {
        return false;
    }
    state->last_sequence = sequence;
    (void)snprintf(state->event_cursor, sizeof(state->event_cursor), "%s:%llu",
                   json_string(data, "instance_id"), (unsigned long long)sequence);
    if (strcmp(type, "task.updated") == 0) {
        apply_task(&state->task, payload);
        (void)snprintf(description, sizeof(description), "任务 #%lld · %s",
                       (long long)state->task.task_id, state->task.status);
        state_add_event(state, "task", description, occurred_at);
    } else if (strcmp(type, "message.updated") == 0) {
        upsert_message(state, payload);
        (void)snprintf(description, sizeof(description), "%s消息 · %s",
                       strcmp(json_string(payload, "role"), "assistant") == 0 ? "机器人" : "用户",
                       json_string(payload, "status"));
        state_add_event(state, "message", description, occurred_at);
    } else if (strcmp(type, "action.updated") == 0) {
        upsert_action(state, payload);
        (void)snprintf(description, sizeof(description), "%s #%lld · %s",
                       json_string(payload, "skill_name"),
                       (long long)json_integer(payload, "action_id"),
                       json_string(payload, "status"));
        state_add_event(state, "action", description, occurred_at);
    } else if (strcmp(type, "observation.created") == 0) {
        apply_observation(&state->observation, payload);
        state->has_observation = true;
        (void)snprintf(description, sizeof(description), "观察完成 · %s",
                       json_string(payload, "summary"));
        state_add_event(state, "vision", description, occurred_at);
    } else if (strcmp(type, "recording.updated") == 0) {
        apply_recording(&state->recording, payload);
        state->has_recording = true;
        (void)snprintf(description, sizeof(description), "录音 · %s", json_string(payload, "status"));
        state_add_event(state, "speech", description, occurred_at);
    } else {
        return false;
    }
    return true;
}

bool state_has_pending_message(const UiState *state) {
    size_t index;
    for (index = 0; index < state->message_count; index++) {
        if (strcmp(state->messages[index].role, "user") == 0 &&
            (strcmp(state->messages[index].status, "accepted") == 0 ||
             strcmp(state->messages[index].status, "processing") == 0)) {
            return true;
        }
    }
    return false;
}

SpeechPhase state_speech_phase(const UiState *state) {
    size_t index;
    if (state->has_recording) {
        if (strcmp(state->recording.status, "recording") == 0) {
            return SPEECH_RECORDING;
        }
        if (strcmp(state->recording.status, "transcribing") == 0) {
            return SPEECH_TRANSCRIBING;
        }
    }
    for (index = state->action_count; index > 0; index--) {
        const ActionRecord *action = &state->actions[index - 1];
        if (strcmp(action->skill_name, "speak") == 0 &&
            strcmp(action->status, "succeeded") != 0 && strcmp(action->status, "failed") != 0 &&
            strcmp(action->status, "cancelled") != 0 && strcmp(action->status, "timed_out") != 0) {
            return SPEECH_SPEAKING;
        }
    }
    if (state_has_pending_message(state)) {
        return SPEECH_PROCESSING;
    }
    /* 只看最近一轮：之前失败过、后来又正常对话的，不再显示失败 */
    {
        const MessageRecord *latest =
            state->message_count > 0 ? &state->messages[state->message_count - 1] : NULL;
        if (state->has_recording && strcmp(state->recording.status, "failed") == 0 &&
            (latest == NULL || state->recording.ended_at >= latest->created_at)) {
            return SPEECH_FAILED;
        }
        if (latest != NULL && latest->error[0] != '\0') {
            return SPEECH_FAILED;
        }
    }
    for (index = state->action_count; index > 0; index--) {
        if (strcmp(state->actions[index - 1].skill_name, "speak") == 0) {
            return strcmp(state->actions[index - 1].status, "failed") == 0 ? SPEECH_FAILED
                                                                            : SPEECH_IDLE;
        }
    }
    return SPEECH_IDLE;
}

bool state_task_active(const UiState *state) {
    return state->task.task_id > 0 &&
           (strcmp(state->task.status, "pending") == 0 ||
            strcmp(state->task.status, "running") == 0 ||
            strcmp(state->task.status, "cancelling") == 0);
}

const char *state_latest_error(const UiState *state) {
    size_t index;
    if (state->system.runtime_blocked_reason[0] != '\0') {
        return state->system.runtime_blocked_reason;
    }
    if (state->has_recording && state->recording.error[0] != '\0') {
        return state->recording.error;
    }
    for (index = state->message_count; index > 0; index--) {
        if (state->messages[index - 1].error[0] != '\0') {
            return state->messages[index - 1].error;
        }
    }
    for (index = state->action_count; index > 0; index--) {
        if (state->actions[index - 1].failure[0] != '\0') {
            return state->actions[index - 1].failure;
        }
    }
    return state->system.camera_error;
}

const UiEvent *state_event_at(const UiState *state, size_t newest_index) {
    size_t index;
    if (newest_index >= state->event_count) {
        return NULL;
    }
    index = (state->event_head + state->event_count - 1 - newest_index) % UI_MAX_EVENTS;
    return &state->events[index];
}
