import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audio_capture import list_input_devices, resolve_input_device


FAKE_DEVICES = [
    {"name": "Output Only", "max_input_channels": 0},
    {"name": "USB PnP Sound Device Mono", "max_input_channels": 1},
    {"name": "Built-in Microphone", "max_input_channels": 2},
]


def test_resolve_default_input_device():
    assert resolve_input_device("default", FAKE_DEVICES) is None
    assert resolve_input_device("", FAKE_DEVICES) is None
    assert resolve_input_device(None, FAKE_DEVICES) is None


def test_resolve_input_device_by_index():
    assert resolve_input_device("1", FAKE_DEVICES) == 1
    assert resolve_input_device(2, FAKE_DEVICES) == 2


def test_resolve_input_device_by_name_substring_case_insensitive():
    assert resolve_input_device("usb pnp", FAKE_DEVICES) == 1


def test_resolve_rejects_output_only_device_index():
    with pytest.raises(ValueError, match="not an available input device"):
        resolve_input_device("0", FAKE_DEVICES)


def test_resolve_rejects_unknown_device_name():
    with pytest.raises(ValueError, match="did not match"):
        resolve_input_device("missing microphone", FAKE_DEVICES)


def test_list_input_devices_filters_output_only_devices():
    assert list_input_devices(FAKE_DEVICES) == [
        {"index": 1, "name": "USB PnP Sound Device Mono", "max_input_channels": 1},
        {"index": 2, "name": "Built-in Microphone", "max_input_channels": 2},
    ]
