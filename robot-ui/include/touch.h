#ifndef ROBOT_UI_TOUCH_H
#define ROBOT_UI_TOUCH_H

/* 触摸屏快速点按：按下与抬起落在同一帧时，Raylib 每帧只比较一次按键状态，
 * 这次点按会整个丢失（界面“点了没反应”）。这里把同一帧内的抬起推迟到下一帧，
 * 保证每次点按都依次产生一帧 Pressed、一帧 Released。 */

/* 在 InitWindow 之后调用一次。 */
void touch_install(void);

/* 每帧开头调用：把上一帧推迟的抬起交给 Raylib。 */
void touch_frame_begin(void);

#endif
