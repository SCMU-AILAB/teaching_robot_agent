#include "touch.h"

#include <stdbool.h>
#include <stddef.h>

/* Raylib 静态库内置 GLFW（rglfw.c），这里只声明用到的三个接口。 */
typedef struct GLFWwindow GLFWwindow;
typedef void (*GLFWmousebuttonfun)(GLFWwindow *window, int button, int action, int mods);
GLFWwindow *glfwGetCurrentContext(void);
GLFWmousebuttonfun glfwSetMouseButtonCallback(GLFWwindow *window, GLFWmousebuttonfun callback);

#define TOUCH_PRESS 1
#define TOUCH_RELEASE 0
#define TOUCH_BUTTONS 8

static GLFWmousebuttonfun raylib_callback = NULL;
static GLFWwindow *touch_window = NULL;
/* 本轮事件轮询中按下、还没有任何一帧看到的键 */
static bool pressed_this_poll[TOUCH_BUTTONS];
/* 与按下同一轮到达的抬起：先让一帧看到按下，再交给 Raylib。
 * held：本帧开头登记，ready：下一帧开头交付。 */
static bool held_release[TOUCH_BUTTONS];
static bool ready_release[TOUCH_BUTTONS];
static int release_mods[TOUCH_BUTTONS];

static void on_mouse_button(GLFWwindow *window, int button, int action, int mods) {
    if (button >= 0 && button < TOUCH_BUTTONS) {
        if (action == TOUCH_PRESS) {
            pressed_this_poll[button] = true;
        } else if (action == TOUCH_RELEASE && pressed_this_poll[button]) {
            held_release[button] = true;
            release_mods[button] = mods;
            return;
        }
    }
    if (raylib_callback != NULL) {
        raylib_callback(window, button, action, mods);
    }
}

void touch_install(void) {
    touch_window = glfwGetCurrentContext();
    if (touch_window != NULL) {
        raylib_callback = glfwSetMouseButtonCallback(touch_window, on_mouse_button);
    }
}

void touch_frame_begin(void) {
    int button;
    for (button = 0; button < TOUCH_BUTTONS; button++) {
        /* 上一帧已看到按下：现在交付抬起，本帧看到 Released */
        if (ready_release[button]) {
            ready_release[button] = false;
            if (raylib_callback != NULL) {
                raylib_callback(touch_window, button, TOUCH_RELEASE, release_mods[button]);
            }
        }
        if (held_release[button]) {
            held_release[button] = false;
            ready_release[button] = true;
        }
        pressed_this_poll[button] = false;
    }
}
