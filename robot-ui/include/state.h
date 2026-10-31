#ifndef ROBOT_UI_STATE_H
#define ROBOT_UI_STATE_H

#include "cJSON.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define UI_ID_CAP 65
#define UI_STATUS_CAP 32
#define UI_TEXT_CAP 8193
#define UI_ERROR_CAP 513
#define UI_MAX_MESSAGES 100
#define UI_MAX_ACTIONS 200
#define UI_MAX_OBJECTS 32
#define UI_MAX_EVENTS 40

typedef struct SystemState {
    char instance_id[UI_ID_CAP];
    int64_t active_task_id;
    bool simulated;
    char runtime_blocked_reason[UI_ERROR_CAP];
    bool robot_connected;
    bool robot_moving;
    double robot_x;
    double robot_y;
    double robot_yaw;
    double robot_updated_at;
    bool camera_available;
    double camera_last_frame_at;
    char camera_error[UI_ERROR_CAP];
    bool audio_available;
    bool audio_recording;
    bool audio_playing;
    char agent[UI_STATUS_CAP];
    char vlm[UI_STATUS_CAP];
    char asr[UI_STATUS_CAP];
    char tts[UI_STATUS_CAP];
} SystemState;

typedef struct TaskRecord {
    int64_t task_id;
    char status[UI_STATUS_CAP];
    char user_target[UI_TEXT_CAP];
    double created_at;
    double ended_at;
} TaskRecord;

typedef struct MessageRecord {
    char message_id[UI_ID_CAP];
    int64_t task_id;
    char role[16];
    char text[UI_TEXT_CAP];
    char source[16];
    char status[UI_STATUS_CAP];
    char reply_to[UI_ID_CAP];
    int64_t speech_action_id;
    char error[UI_ERROR_CAP];
    double created_at;
} MessageRecord;

typedef struct ActionRecord {
    int64_t action_id;
    char skill_name[64];
    char status[UI_STATUS_CAP];
    double created_at;
    double started_at;
    double ended_at;
    char summary[UI_TEXT_CAP];
    char failure[UI_ERROR_CAP];
} ActionRecord;

typedef struct ObservationObject {
    char label[256];
    char position[32];
} ObservationObject;

typedef struct ObservationRecord {
    char observation_id[UI_ID_CAP];
    int64_t task_id;
    char question[UI_TEXT_CAP];
    char summary[UI_TEXT_CAP];
    ObservationObject objects[UI_MAX_OBJECTS];
    size_t object_count;
    double captured_at;
    double analyzed_at;
    bool stale;
    bool simulated;
    char frame_evidence_id[UI_ID_CAP];
} ObservationRecord;

typedef struct RecordingRecord {
    char recording_id[UI_ID_CAP];
    int64_t task_id;
    char status[UI_STATUS_CAP];
    double started_at;
    double ended_at;
    char transcript[UI_TEXT_CAP];
    char message_id[UI_ID_CAP];
    char error[UI_ERROR_CAP];
} RecordingRecord;

typedef struct UiEvent {
    double occurred_at;
    char kind[32];
    char text[512];
} UiEvent;

typedef enum SpeechPhase {
    SPEECH_IDLE = 0,
    SPEECH_RECORDING,
    SPEECH_TRANSCRIBING,
    SPEECH_PROCESSING,
    SPEECH_SPEAKING,
    SPEECH_FAILED
} SpeechPhase;

typedef struct UiState {
    SystemState system;
    TaskRecord task;
    MessageRecord messages[UI_MAX_MESSAGES];
    size_t message_count;
    ActionRecord actions[UI_MAX_ACTIONS];
    size_t action_count;
    ObservationRecord observation;
    bool has_observation;
    RecordingRecord recording;
    bool has_recording;
    UiEvent events[UI_MAX_EVENTS];
    size_t event_count;
    size_t event_head;
    char event_cursor[96];
    uint64_t last_sequence;
    bool backend_connected;
    bool snapshot_loaded;
    bool truncated_messages;
    bool truncated_actions;
} UiState;

void state_init(UiState *state);
void state_clear_task(UiState *state);
bool state_apply_system(UiState *state, const cJSON *data);
bool state_apply_snapshot(UiState *state, const cJSON *data);
bool state_apply_event(UiState *state, const cJSON *data);
void state_add_event(UiState *state, const char *kind, const char *text, double occurred_at);
SpeechPhase state_speech_phase(const UiState *state);
bool state_task_active(const UiState *state);
bool state_has_pending_message(const UiState *state);
const char *state_latest_error(const UiState *state);
const UiEvent *state_event_at(const UiState *state, size_t newest_index);

#endif
