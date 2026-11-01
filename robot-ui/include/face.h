#ifndef ROBOT_UI_FACE_H
#define ROBOT_UI_FACE_H

#include "raylib.h"
#include "state.h"

/*
 * 绘制宣传片中的机器人表情：白色胶囊眼与橙色嘴线，随语音阶段变化。
 * framed 为 true 时绘制黑色屏幕和边框（顶栏小脸）；为 false 时只画五官（待机全屏）。
 * opacity 取 0..1，用于待机与唤醒的淡入淡出。
 */
void face_draw(Rectangle bounds, SpeechPhase phase, bool online, double time_s,
               bool framed, float opacity);

#endif
