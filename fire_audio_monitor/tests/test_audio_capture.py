import sys
import wave
import subprocess
import threading
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audio_capture import (
    ArecordCapture,
    SoundDeviceCapture,
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


def test_build_arecord_command_with_default_device():
    command = build_arecord_command("default", 16000, 3, Path("/tmp/fire_audio_monitor_sample.wav"))

    assert command == [
        "arecord",
        "-D",
        "default",
        "-f",
        "S16_LE",
        "-r",
        "16000",
        "-c",
        "1",
        "-d",
        "3",
        "/tmp/fire_audio_monitor_sample.wav",
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


class FakeStream:
    instances = []

    def __init__(self, callback, **kwargs):
        self.callback = callback
        self.started = False
        self.stopped = False
        self.closed = False
        self.instances.append(self)

    def start(self):
        self.started = True
        self.stopped = False
        self.callback(np.ones((16000, 1), dtype=np.float32), 16000, None, None)

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def test_sounddevice_stream_reused_and_closed(monkeypatch):
    FakeStream.instances.clear()
    monkeypatch.setattr("app.audio_capture.resolve_input_device", lambda value: None)
    monkeypatch.setattr("app.audio_capture.sd", SimpleNamespace(InputStream=FakeStream))
    backend = SoundDeviceCapture(1)
    backend.capture(1)
    backend.capture(1)
    assert len(FakeStream.instances) == 1
    assert FakeStream.instances[0].stopped
    backend.close()
    assert FakeStream.instances[0].stopped and FakeStream.instances[0].closed


def test_sounddevice_discards_audio_between_capture_generations(monkeypatch):
    FakeStream.instances.clear()
    monkeypatch.setattr("app.audio_capture.resolve_input_device", lambda value: None)
    monkeypatch.setattr("app.audio_capture.sd", SimpleNamespace(InputStream=FakeStream))
    backend = SoundDeviceCapture(1)
    first, _ = backend.capture(1)
    stream = FakeStream.instances[0]
    for _ in range(20):  # Simulated audio during the configured sample interval.
        stream.callback(np.zeros((16000, 1), dtype=np.float32), 16000, None, "overflow")
    assert backend._chunks.empty()
    assert not backend._overflowed.is_set()
    second, _ = backend.capture(1)
    np.testing.assert_array_equal(first, np.ones(16000, dtype=np.float32))
    np.testing.assert_array_equal(second, np.ones(16000, dtype=np.float32))
    assert stream.stopped


def test_sounddevice_exact_frame_count_and_trims_final_block(monkeypatch):
    class UnevenStream(FakeStream):
        def start(self):
            self.started = True
            self.stopped = False
            self.callback(np.ones((6, 1), dtype=np.float32), 6, None, None)
            self.callback(np.full((6, 1), 2, dtype=np.float32), 6, None, None)

    FakeStream.instances.clear()
    monkeypatch.setattr("app.audio_capture.resolve_input_device", lambda value: None)
    monkeypatch.setattr("app.audio_capture.sd", SimpleNamespace(InputStream=UnevenStream))
    samples, _ = SoundDeviceCapture(1, sample_rate_hz=10).capture(1)
    assert len(samples) == 10
    np.testing.assert_array_equal(samples, np.r_[np.ones(6), np.full(4, 2)])


def test_sounddevice_callback_status_cleared_for_next_capture(monkeypatch):
    class StatusOnceStream(FakeStream):
        starts = 0

        def start(self):
            self.started = True
            self.stopped = False
            type(self).starts += 1
            status = "input overflow" if self.starts == 1 else None
            self.callback(np.ones((16000, 1), dtype=np.float32), 16000, None, status)

    FakeStream.instances.clear()
    StatusOnceStream.starts = 0
    monkeypatch.setattr("app.audio_capture.resolve_input_device", lambda value: None)
    monkeypatch.setattr("app.audio_capture.sd", SimpleNamespace(InputStream=StatusOnceStream))
    backend = SoundDeviceCapture(1)
    with pytest.raises(RuntimeError, match="callback status"):
        backend.capture(1)
    assert FakeStream.instances[0].stopped and FakeStream.instances[0].closed
    assert backend.capture(1)[0].shape == (16000,)


def test_sounddevice_recreates_after_start_and_stop_failures(monkeypatch):
    class LifecycleStream(FakeStream):
        starts = 0
        stops = 0

        def start(self):
            type(self).starts += 1
            if self.starts == 1:
                raise RuntimeError("start failed")
            super().start()

        def stop(self):
            type(self).stops += 1
            super().stop()
            if self.stops == 1:
                raise RuntimeError("stop failed")

    FakeStream.instances.clear()
    LifecycleStream.starts = LifecycleStream.stops = 0
    monkeypatch.setattr("app.audio_capture.resolve_input_device", lambda value: None)
    monkeypatch.setattr("app.audio_capture.sd", SimpleNamespace(InputStream=LifecycleStream))
    backend = SoundDeviceCapture(1)
    with pytest.raises(RuntimeError, match="start failed"):
        backend.capture(1)
    with pytest.raises(RuntimeError, match="Could not stop"):
        backend.capture(1)
    assert backend.capture(1)[0].shape == (16000,)
    assert len(FakeStream.instances) == 3


def test_sounddevice_exception_closes_and_reopens(monkeypatch):
    FakeStream.instances.clear()
    monkeypatch.setattr("app.audio_capture.resolve_input_device", lambda value: None)
    class SilentStream(FakeStream):
        def start(self):
            self.started = True

    monkeypatch.setattr("app.audio_capture.sd", SimpleNamespace(InputStream=SilentStream))
    backend = SoundDeviceCapture(1)
    with pytest.raises(TimeoutError):
        backend.capture(0.001)
    assert FakeStream.instances[0].closed
    monkeypatch.setattr("app.audio_capture.sd", SimpleNamespace(InputStream=FakeStream))
    backend.capture(1)
    assert len(FakeStream.instances) == 2
    backend.restart()
    assert FakeStream.instances[-1].closed


def test_sounddevice_callback_overflow_is_nonblocking_and_discards_capture(monkeypatch):
    class BurstStream(FakeStream):
        def start(self):
            self.started = True
            for _ in range(17):
                self.callback(np.ones((100, 1), dtype=np.float32), 100, None, None)

    FakeStream.instances.clear()
    monkeypatch.setattr("app.audio_capture.resolve_input_device", lambda value: None)
    monkeypatch.setattr("app.audio_capture.sd", SimpleNamespace(InputStream=BurstStream))
    backend = SoundDeviceCapture(1)
    with pytest.raises(RuntimeError, match="queue overflowed"):
        backend.capture(0.1)
    assert backend.callback_overflows == 1
    assert FakeStream.instances[0].closed


def test_sounddevice_close_wakes_blocked_capture(monkeypatch):
    class SilentStream(FakeStream):
        def start(self):
            self.started = True

    FakeStream.instances.clear()
    monkeypatch.setattr("app.audio_capture.resolve_input_device", lambda value: None)
    monkeypatch.setattr("app.audio_capture.sd", SimpleNamespace(InputStream=SilentStream))
    backend = SoundDeviceCapture(1)
    errors = []
    worker = threading.Thread(target=lambda: _capture_error(backend, errors))
    worker.start()
    for _ in range(100):
        if FakeStream.instances:
            break
        threading.Event().wait(0.001)
    backend.close()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert isinstance(errors[0], InterruptedError)


def _capture_error(backend, errors):
    try:
        backend.capture(5)
    except Exception as exc:
        errors.append(exc)


def test_callbacks_from_closed_stream_cannot_feed_replacement(monkeypatch):
    class ManualStream(FakeStream):
        def start(self):
            self.started = True

    FakeStream.instances.clear()
    monkeypatch.setattr("app.audio_capture.resolve_input_device", lambda value: None)
    monkeypatch.setattr("app.audio_capture.sd", SimpleNamespace(InputStream=ManualStream))
    backend = SoundDeviceCapture(1)
    results = []
    first = threading.Thread(target=lambda: results.append(backend.capture(1)[0]))
    first.start()
    while len(FakeStream.instances) < 1:
        threading.Event().wait(0.001)
    old_stream = FakeStream.instances[0]
    old_stream.callback(np.ones((16000, 1), dtype=np.float32), 16000, None, None)
    first.join(timeout=1)
    backend.close()

    second = threading.Thread(target=lambda: results.append(backend.capture(1)[0]))
    second.start()
    while len(FakeStream.instances) < 2:
        threading.Event().wait(0.001)
    old_stream.callback(np.zeros((16000, 1), dtype=np.float32), 16000, None, None)
    threading.Event().wait(0.01)
    assert second.is_alive()
    FakeStream.instances[1].callback(np.full((16000, 1), 2, dtype=np.float32), 16000, None, None)
    second.join(timeout=1)
    assert not second.is_alive()
    np.testing.assert_array_equal(results[1], np.full(16000, 2, dtype=np.float32))


class TimeoutProcess:
    def __init__(self, *args, **kwargs):
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.calls = 0

    def wait(self, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise subprocess.TimeoutExpired("arecord", timeout)
        self.returncode = -15
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_arecord_timeout_terminates_reaps_and_removes_temp(monkeypatch, tmp_path):
    process = TimeoutProcess()
    monkeypatch.setattr("app.audio_capture.subprocess.Popen", lambda *a, **k: process)
    backend = ArecordCapture(1, temp_dir=str(tmp_path))
    with pytest.raises(TimeoutError):
        backend.capture(0.01)
    assert process.terminated
    assert process.calls == 2
    assert not list(tmp_path.glob("fire_audio_monitor_*.wav"))


def test_arecord_failure_removes_temp(monkeypatch, tmp_path):
    process = TimeoutProcess()
    process.wait = lambda timeout=None: 1
    process.returncode = 1
    monkeypatch.setattr("app.audio_capture.subprocess.Popen", lambda *a, **k: process)
    backend = ArecordCapture(1, temp_dir=str(tmp_path))
    with pytest.raises(RuntimeError):
        backend.capture(1)
    assert not list(tmp_path.glob("fire_audio_monitor_*.wav"))


def test_arecord_success_removes_temp(monkeypatch, tmp_path):
    class SuccessProcess:
        returncode = 0

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return self.returncode

    def start(command, **kwargs):
        with wave.open(command[-1], "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(np.zeros(16, dtype="<i2").tobytes())
        return SuccessProcess()

    monkeypatch.setattr("app.audio_capture.subprocess.Popen", start)
    backend = ArecordCapture(1, temp_dir=str(tmp_path))
    samples, rate = backend.capture(1)
    assert rate == 16000
    assert len(samples) == 16
    assert not list(tmp_path.glob("fire_audio_monitor_*.wav"))
