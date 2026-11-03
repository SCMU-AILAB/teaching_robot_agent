#include "text.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

void text_copy(char *destination, size_t capacity, const char *source) {
    if (destination == NULL || capacity == 0) {
        return;
    }
    if (source == NULL) {
        destination[0] = '\0';
        return;
    }
    (void)snprintf(destination, capacity, "%s", source);
}

size_t utf8_encode(int codepoint, char output[5]) {
    if (codepoint < 0 || codepoint > 0x10FFFF ||
        (codepoint >= 0xD800 && codepoint <= 0xDFFF)) {
        return 0;
    }
    if (codepoint <= 0x7F) {
        output[0] = (char)codepoint;
        output[1] = '\0';
        return 1;
    }
    if (codepoint <= 0x7FF) {
        output[0] = (char)(0xC0 | (codepoint >> 6));
        output[1] = (char)(0x80 | (codepoint & 0x3F));
        output[2] = '\0';
        return 2;
    }
    if (codepoint <= 0xFFFF) {
        output[0] = (char)(0xE0 | (codepoint >> 12));
        output[1] = (char)(0x80 | ((codepoint >> 6) & 0x3F));
        output[2] = (char)(0x80 | (codepoint & 0x3F));
        output[3] = '\0';
        return 3;
    }
    output[0] = (char)(0xF0 | (codepoint >> 18));
    output[1] = (char)(0x80 | ((codepoint >> 12) & 0x3F));
    output[2] = (char)(0x80 | ((codepoint >> 6) & 0x3F));
    output[3] = (char)(0x80 | (codepoint & 0x3F));
    output[4] = '\0';
    return 4;
}

bool utf8_append(char *buffer, size_t capacity, int codepoint) {
    char encoded[5];
    size_t length = utf8_encode(codepoint, encoded);
    size_t used = strlen(buffer);
    if (length == 0 || used + length >= capacity) {
        return false;
    }
    memcpy(buffer + used, encoded, length + 1);
    return true;
}

bool utf8_backspace(char *buffer) {
    size_t length = strlen(buffer);
    if (length == 0) {
        return false;
    }
    /* 退到最后一个字符的首字节再截断，删中文时不留下半个字 */
    do {
        length--;
    } while (length > 0 && (((unsigned char)buffer[length] & 0xC0U) == 0x80U));
    buffer[length] = '\0';
    return true;
}

size_t utf8_length(const char *text) {
    size_t count = 0;
    const unsigned char *cursor = (const unsigned char *)text;
    while (*cursor != '\0') {
        if ((*cursor & 0xC0U) != 0x80U) {
            count++;
        }
        cursor++;
    }
    return count;
}

void format_time(double unix_seconds, char output[32]) {
    time_t value;
    struct tm result;
    if (unix_seconds <= 0) {
        text_copy(output, 32, "--");
        return;
    }
    value = (time_t)unix_seconds;
    if (localtime_r(&value, &result) == NULL ||
        strftime(output, 32, "%m-%d %H:%M:%S", &result) == 0) {
        text_copy(output, 32, "--");
    }
}

void format_duration(double seconds, char output[32]) {
    if (seconds < 0) {
        text_copy(output, 32, "--");
        return;
    }
    if (seconds < 60.0) {
        (void)snprintf(output, 32, "%.1f s", seconds);
    } else {
        (void)snprintf(output, 32, "%d m %02d s", (int)seconds / 60,
                       (int)seconds % 60);
    }
}

static int base64_value(char symbol) {
    if (symbol >= 'A' && symbol <= 'Z') return symbol - 'A';
    if (symbol >= 'a' && symbol <= 'z') return symbol - 'a' + 26;
    if (symbol >= '0' && symbol <= '9') return symbol - '0' + 52;
    if (symbol == '+') return 62;
    if (symbol == '/') return 63;
    return -1;
}

size_t base64_decode(const char *input, unsigned char *output, size_t capacity) {
    size_t written = 0;
    unsigned int buffer = 0;
    int bits = 0;
    const char *cursor;
    for (cursor = input; *cursor != '\0'; cursor++) {
        int value;
        if (*cursor == '=') {
            break;
        }
        value = base64_value(*cursor);
        if (value < 0) {
            return (size_t)-1;
        }
        buffer = (buffer << 6) | (unsigned int)value;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            if (written >= capacity) {
                return (size_t)-1;
            }
            output[written++] = (unsigned char)((buffer >> bits) & 0xFFu);
        }
    }
    return written;
}
