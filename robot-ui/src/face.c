#include "face.h"
#include "theme.h"

#include <math.h>

#define FACE_BLINK_PERIOD_S 4.2
#define FACE_BLINK_LENGTH_S 0.14

static float blink_scale(double time_s) {
    double phase = fmod(time_s, FACE_BLINK_PERIOD_S);
    if (phase > FACE_BLINK_LENGTH_S) {
        return 1.0f;
    }
    /* 闭合到 12% 再回弹，避免眨眼生硬。 */
    return 0.12f + 0.88f * fabsf((float)(phase / FACE_BLINK_LENGTH_S) * 2.0f - 1.0f);
}

void face_draw(Rectangle bounds, SpeechPhase phase, bool online, double time_s,
               bool framed, float opacity) {
    Rectangle eye_left;
    Rectangle eye_right;
    Rectangle mouth;
    /* 待机画面始终呈现宣传片的表情；连接状态在唤醒后的界面里显示。 */
    if (!framed) {
        online = true;
    }
    Color eye_color = online ? (Color){250, 250, 250, 255} : (Color){110, 110, 114, 255};
    Color mouth_color = phase == SPEECH_FAILED ? COLOR_ERROR : COLOR_ACCENT;
    float eye_w = bounds.width * 0.14f;
    float eye_h = bounds.height * 0.36f;
    float eye_cy = bounds.y + bounds.height * 0.42f;
    float shift = 0.0f;
    float mouth_w = eye_w * 0.55f;
    float mouth_h = fmaxf(2.0f, eye_w * 0.06f);
    float scale = blink_scale(time_s);

    if (phase == SPEECH_RECORDING) {
        /* 聆听：眼睛略微拉高。 */
        eye_h *= 1.15f;
    } else if (phase == SPEECH_TRANSCRIBING || phase == SPEECH_PROCESSING) {
        /* 思考：视线左右缓慢游移。 */
        shift = sinf((float)time_s * 2.4f) * bounds.width * 0.03f;
    } else if (phase == SPEECH_SPEAKING) {
        mouth_w = eye_w * (0.55f + 0.75f * (0.5f + 0.5f * sinf((float)time_s * 14.0f)));
        mouth_h = fmaxf(2.0f, eye_w * 0.14f);
    } else if (phase == SPEECH_FAILED) {
        eye_h *= 0.7f;
    } else if (!framed) {
        /* 待机：视线极缓慢地左右漂移，让画面保持“活着”。 */
        shift = sinf((float)time_s * 0.6f) * bounds.width * 0.012f;
    }
    if (!online) {
        scale = 0.18f;
    }
    eye_h *= scale;
    eye_left = (Rectangle){bounds.x + bounds.width * 0.36f - eye_w * 0.5f + shift,
                           eye_cy - eye_h * 0.5f, eye_w, eye_h};
    eye_right = (Rectangle){bounds.x + bounds.width * 0.64f - eye_w * 0.5f + shift,
                            eye_cy - eye_h * 0.5f, eye_w, eye_h};
    mouth = (Rectangle){bounds.x + (bounds.width - mouth_w) * 0.5f + shift * 0.5f,
                        bounds.y + bounds.height * 0.76f, mouth_w, mouth_h};

    if (framed) {
        DrawRectangleRounded(bounds, 0.12f, 8, Fade((Color){4, 4, 5, 255}, opacity));
        DrawRectangleRoundedLinesEx(bounds, 0.12f, 8, 1.5f, Fade(COLOR_BEZEL, opacity));
    }
    DrawRectangleRounded(eye_left, 1.0f, 12, Fade(eye_color, opacity));
    DrawRectangleRounded(eye_right, 1.0f, 12, Fade(eye_color, opacity));
    DrawRectangleRounded(mouth, 1.0f, 8, Fade(mouth_color, opacity));
}
