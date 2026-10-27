# tests/test_audio_device_resolution.py
"""校验设备按名称解析：序号随拔插变化，配置不得依赖序号."""

import unittest

from providers.audio.worker import resolve_device


class _FakeDriver:
    """提供 sounddevice.query_devices 最小替身，不接触真实声卡."""

    _devices: list[dict[str, object]]

    def __init__(self, devices: list[dict[str, object]]) -> None:
        """保存固定设备表，供解析逻辑按名称筛选."""
        self._devices = devices

    def query_devices(self) -> list[dict[str, object]]:
        """返回固定设备表，元素字段与 sounddevice 保持一致."""
        return self._devices


def _device(name: str, inputs: int, outputs: int) -> dict[str, object]:
    """构造一个设备条目，只填写解析所需字段."""
    return {
        "name": name,
        "max_input_channels": inputs,
        "max_output_channels": outputs,
    }


def _sample_devices() -> list[dict[str, object]]:
    """返回两个设备：纯输出扬声器与双向耳机."""
    return [
        _device("USB Audio Device: - (hw:2,0)", 0, 2),
        _device("Earpods: - (hw:3,0)", 2, 2),
    ]


class ResolveDeviceTest(unittest.TestCase):
    """覆盖名称匹配、方向筛选与歧义拒绝."""

    def test_empty_name_uses_system_default(self) -> None:
        """空名称表示系统默认设备."""
        driver = _FakeDriver(_sample_devices())

        self.assertIsNone(resolve_device(driver, "", output=False))
        self.assertIsNone(resolve_device(driver, "   ", output=True))

    def test_exact_name_matches_index(self) -> None:
        """完整名称直接命中对应序号."""
        driver = _FakeDriver(_sample_devices())

        self.assertEqual(resolve_device(driver, "Earpods: - (hw:3,0)", output=True), 1)

    def test_partial_and_case_insensitive_name(self) -> None:
        """允许子串匹配并忽略大小写，便于配置简短名称."""
        driver = _FakeDriver(_sample_devices())

        self.assertEqual(resolve_device(driver, "earpods", output=False), 1)
        self.assertEqual(resolve_device(driver, "Ear", output=True), 1)

    def test_direction_filters_devices_without_channels(self) -> None:
        """按方向筛选：纯输出扬声器不能用作录音设备."""
        driver = _FakeDriver(_sample_devices())

        self.assertEqual(resolve_device(driver, "USB Audio Device", output=True), 0)
        with self.assertRaises(ValueError):
            _ = resolve_device(driver, "USB Audio Device", output=False)

    def test_unknown_name_fails_instead_of_default(self) -> None:
        """找不到设备时明确失败，不退回系统默认设备."""
        driver = _FakeDriver(_sample_devices())

        with self.assertRaises(ValueError):
            _ = resolve_device(driver, "Nonexistent Mic", output=False)

    def test_ambiguous_name_is_rejected(self) -> None:
        """同名设备多于一个时拒绝猜测，要求配置更精确的名称."""
        driver = _FakeDriver(
            [
                _device("USB Audio Device: - (hw:2,0)", 1, 2),
                _device("USB Audio Device: - (hw:4,0)", 1, 2),
            ]
        )

        with self.assertRaises(ValueError):
            _ = resolve_device(driver, "USB Audio Device", output=False)

    def test_exact_match_wins_over_partial(self) -> None:
        """存在完整名称时优先命中，不因同名子串判为歧义."""
        driver = _FakeDriver(
            [
                _device("Earpods", 1, 1),
                _device("Earpods Pro", 1, 1),
            ]
        )

        self.assertEqual(resolve_device(driver, "Earpods", output=False), 0)
