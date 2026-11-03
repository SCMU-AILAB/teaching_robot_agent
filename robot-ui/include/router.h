#ifndef ROBOT_UI_ROUTER_H
#define ROBOT_UI_ROUTER_H

typedef enum ScreenId {
    SCREEN_DASHBOARD = 0,
    SCREEN_ASSISTANT,
    SCREEN_VISION,
    SCREEN_MONITOR,
    SCREEN_MAP,
    SCREEN_COUNT
} ScreenId;

typedef struct Router {
    ScreenId current;
    double transition_remaining;
} Router;

void router_init(Router *router);
void router_go(Router *router, ScreenId screen);
void router_update(Router *router, double delta_seconds);
const char *router_title(ScreenId screen);

#endif
