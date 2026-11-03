#ifndef ROBOT_UI_TEXT_H
#define ROBOT_UI_TEXT_H

#include <stdbool.h>
#include <stddef.h>

void text_copy(char *destination, size_t capacity, const char *source);
size_t utf8_encode(int codepoint, char output[5]);
bool utf8_append(char *buffer, size_t capacity, int codepoint);
bool utf8_backspace(char *buffer);
size_t utf8_length(const char *text);
void format_time(double unix_seconds, char output[32]);
void format_duration(double seconds, char output[32]);
/* 解码标准 base64；返回字节数，输入非法或超出 capacity 时返回 (size_t)-1 */
size_t base64_decode(const char *input, unsigned char *output, size_t capacity);

#endif
