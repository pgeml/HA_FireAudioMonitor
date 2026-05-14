import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audio_capture import (
    build_arecord_command,
    capture_audio,
    list_input_devices,
    read_int16_wav_as_float32,
    resolve_input_device,
)


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


def test_resolve_allows_alsa_style_device_strings():
    assert resolve_input_device("hw:1,0", FAKE_DEVICES) == "hw:1,0"
    assert resolve_input_device("plughw:1,0", FAKE_DEVICES) == "plughw:1,0"


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


def test_build_arecord_command():
    command = build_arecord_command("plughw:1,0", 16000, 3, Path("/tmp/sample.wav"))

    assert command == [
        "arecord",
        "-D",
        "plughw:1,0",
        "-f",
        "S16_LE",
        "-r",
        "16000",
        "-c",
        "1",
        "-d",
        "3",
        "/tmp/sample.wav",
    ]


def test_read_int16_wav_as_float32(tmp_path):
    sample_path = tmp_path / "sample.wav"
    int_samples = np.array([-32768, -16384, 0, 16384, 32767], dtype="<i2")
    with wave.open(str(sample_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(int_samples.tobytes())

    samples, sample_rate_hz = read_int16_wav_as_float32(sample_path)

    assert sample_rate_hz == 16000
    assert samples.dtype == np.float32
    np.testing.assert_allclose(
        samples,
        np.array([-1.0, -0.5, 0.0, 0.5, 32767 / 32768], dtype=np.float32),
    )


def test_capture_audio_dispatches_to_arecord(monkeypatch):
    def fake_arecord(record_seconds, sample_rate_hz, audio_input_device):
        return np.array([0.1], dtype=np.float32), sample_rate_hz

    monkeypatch.setattr("app.audio_capture.capture_audio_arecord", fake_arecord)

    samples, sample_rate_hz = capture_audio(
        3,
        sample_rate_hz=16000,
        audio_input_device="plughw:1,0",
        audio_capture_backend="arecord",
    )

    assert sample_rate_hz == 16000
    np.testing.assert_allclose(samples, np.array([0.1], dtype=np.float32))


def test_capture_audio_dispatches_to_sounddevice(monkeypatch):
    def fake_sounddevice(record_seconds, sample_rate_hz, audio_input_device):
        return np.array([0.2], dtype=np.float32), sample_rate_hz

    monkeypatch.setattr("app.audio_capture.capture_audio_sounddevice", fake_sounddevice)

    samples, sample_rate_hz = capture_audio(
        3,
        sample_rate_hz=16000,
        audio_input_device="USB PnP",
        audio_capture_backend="sounddevice",
    )

    assert sample_rate_hz == 16000
    np.testing.assert_allclose(samples, np.array([0.2], dtype=np.float32))
