# domain/validation.py
"""在运行时校验外部输入，同时把通过校验的值转换为明确类型."""

import math
from typing import cast


def finite_float(value: object, name: str) -> float:
    """校验有限数值并转换为浮点数，拒绝布尔值和溢出值."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def string_key_dict(value: object, name: str) -> dict[str, object]:
    """校验字典及字符串键，返回独立的顶层字典."""
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a dictionary")
    # isinstance 已确认容器种类；键和值在通过下方检查之前仍视为 object。
    entries = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for key, item in entries.items():
        if not isinstance(key, str):
            raise ValueError(f"{name} must have string keys")
        result[key] = item
    return result


def positive_int(value: object, name: str) -> int:
    """校验并返回正整数，拒绝布尔值."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def nonempty_string(value: object, name: str) -> str:
    """校验非空文本并保留原始内容."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value
