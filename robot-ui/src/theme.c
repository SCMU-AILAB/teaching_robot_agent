#include "theme.h"
#include "text.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static Font load_ascii_font(const char *path, bool *owned) {
    Font font = {0};
    if (path != NULL && FileExists(path)) {
        font = LoadFontEx(path, 32, NULL, 0);
        if (font.texture.id != 0) {
            *owned = true;
            SetTextureFilter(font.texture, TEXTURE_FILTER_BILINEAR);
            return font;
        }
    }
    *owned = false;
    return GetFontDefault();
}

static Font load_cjk_font(const AppConfig *config, bool *owned) {
    const char *candidates[] = {
        config->font_body,
        "assets/fonts/NotoSansSC.ttf",
        "../frontend/assets/NotoSansSC.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
    };
    int *codepoints;
    int count = 0;
    int value;
    Font font = {0};
    size_t index;
    codepoints = malloc(sizeof(int) * 22000);
    if (codepoints == NULL) {
        *owned = false;
        return GetFontDefault();
    }
    for (value = 32; value <= 126; value++) {
        codepoints[count++] = value;
    }
    /* Latin-1 补充区含间隔号 · 和乘号 ×。 */
    for (value = 0xA0; value <= 0xFF; value++) {
        codepoints[count++] = value;
    }
    /* 常用标点区含 – … 等；再补不等号 ≠。 */
    for (value = 0x2010; value <= 0x2027; value++) {
        codepoints[count++] = value;
    }
    codepoints[count++] = 0x2260;
    for (value = 0x3000; value <= 0x303F; value++) {
        codepoints[count++] = value;
    }
    for (value = 0x4E00; value <= 0x9FFF; value++) {
        codepoints[count++] = value;
    }
    for (value = 0xFF00; value <= 0xFFEF; value++) {
        codepoints[count++] = value;
    }
    for (index = 0; index < sizeof(candidates) / sizeof(candidates[0]); index++) {
        if (!FileExists(candidates[index])) {
            continue;
        }
        font = LoadFontEx(candidates[index], 24, codepoints, count);
        if (font.texture.id != 0) {
            *owned = true;
            SetTextureFilter(font.texture, TEXTURE_FILTER_BILINEAR);
            break;
        }
    }
    free(codepoints);
    if (font.texture.id == 0) {
        *owned = false;
        font = GetFontDefault();
        TraceLog(LOG_WARNING, "未找到中文字体，使用 Raylib 默认字体；中文可能无法显示");
    }
    return font;
}

void theme_load(Theme *theme, const AppConfig *config) {
    const char *display_fallback = "/usr/share/fonts/truetype/inter/Inter-Regular.ttf";
    const char *mono_fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf";
    memset(theme, 0, sizeof(*theme));
    theme->body = load_cjk_font(config, &theme->body_owned);
    theme->display = load_ascii_font(config->font_display, &theme->display_owned);
    if (!theme->display_owned) {
        theme->display = load_ascii_font(display_fallback, &theme->display_owned);
    }
    if (!theme->display_owned) {
        theme->display = theme->body;
    }
    theme->mono = load_ascii_font(config->font_mono, &theme->mono_owned);
    if (!theme->mono_owned) {
        theme->mono = load_ascii_font(mono_fallback, &theme->mono_owned);
    }
    if (!theme->mono_owned) {
        theme->mono = theme->display;
    }
}

void theme_unload(Theme *theme) {
    if (theme->mono_owned) {
        UnloadFont(theme->mono);
    }
    if (theme->display_owned) {
        UnloadFont(theme->display);
    }
    if (theme->body_owned) {
        UnloadFont(theme->body);
    }
    memset(theme, 0, sizeof(*theme));
}
