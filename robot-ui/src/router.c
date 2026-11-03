#include "router.h"

void router_init(Router *router) {
    router->current = SCREEN_DASHBOARD;
    router->transition_remaining = 0.0;
}

void router_go(Router *router, ScreenId screen) {
    if (screen < 0 || screen >= SCREEN_COUNT || router->current == screen) {
        return;
    }
    router->current = screen;
    router->transition_remaining = 0.18;
}

void router_update(Router *router, double delta_seconds) {
    if (router->transition_remaining > 0.0) {
        router->transition_remaining -= delta_seconds;
        if (router->transition_remaining < 0.0) {
            router->transition_remaining = 0.0;
        }
    }
}

const char *router_title(ScreenId screen) {
    static const char *titles[] = {"总览", "对话", "视野", "监控", "地图"};
    return screen >= 0 && screen < SCREEN_COUNT ? titles[screen] : "";
}
