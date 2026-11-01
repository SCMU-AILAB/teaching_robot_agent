#include "text.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    char text[64] = "";
    char time_text[32];
    assert(utf8_append(text, sizeof(text), 'A'));
    assert(utf8_append(text, sizeof(text), 0x4E2D));
    assert(utf8_append(text, sizeof(text), 0x1F600));
    assert(utf8_length(text) == 3);
    assert(utf8_backspace(text));
    assert(utf8_length(text) == 2);
    assert(utf8_backspace(text));
    assert(strcmp(text, "A") == 0);
    assert(!utf8_append(text, 2, 0x4E2D));
    text_copy(text, sizeof(text), "你好");
    assert(utf8_backspace(text));
    assert(strcmp(text, "你") == 0);
    assert(utf8_backspace(text));
    assert(text[0] == '\0');
    assert(!utf8_backspace(text));
    format_time(0, time_text);
    assert(strcmp(time_text, "--") == 0);
    format_duration(65.0, time_text);
    assert(strcmp(time_text, "1 m 05 s") == 0);
    (void)puts("test_text: OK");
    return 0;
}
