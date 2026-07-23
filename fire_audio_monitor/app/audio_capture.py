from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
import wave
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable

import numpy as np

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - runtime dependency is installed in the add-on image.
    sd = None


LOGGER = logging.getLogger(__name__)
DEFAULT_SAMPLE_RATE_HZ = 16000
_AUDIO_ENV_KEYS = ("PULSE_SERVER", "PULSE_COOKIE", "ALSA_CARD", "AUDIODEV")
_AUDIO_DEVICE_PATHS = (
    Path("/dev/snd"),
    Path("/dev/snd/controlC0"),
    Path("/dev/snd/controlC1"),
    Path("/dev/snd/pcmC1D0c"),
    Path("/dev/snd/by-id"),
    Path("/run/audio"),
    Path("/run/dbus"),
    Path("/run/pulse"),
    Path("/run/user/0/pulse"),
    Path("/tmp/pulse"),
)
_PROC_ASOUND_PATHS = (
    Path("/proc/asound/cards"),
    Path("/proc/asound/devices"),
    Path("/proc/asound/pcm"),
    Path("/proc/asound/version"),
)


class AudioCaptureBackend:
    """Owned audio resource with deterministic capture and shutdown."""

    restart_count = 0

    def capture(self, timeout_seconds: float) -> tuple[np.ndarray, int]:
        raise NotImplementedError

    def restart(self) -> None:
        self.close()
        self.restart_count += 1

    def close(self) -> None:
        raise NotImplementedError


class SoundDeviceCapture(AudioCaptureBackend):
    def __init__(self, record_seconds: int, sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
                 audio_input_device: str | int | None = "default") -> None:
        if sd is None:
            raise RuntimeError("sounddevice is not installed")
        self.record_seconds = record_seconds
        self.sample_rate_hz = sample_rate_hz
        self.frames = int(record_seconds * sample_rate_hz)
        self.device = resolve_input_device(audio_input_device)
        self._stream: Any = None
        self._chunks: queue.Queue[Any] = queue.Queue(maxsize=16)
        self._capture_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._overflowed = threading.Event()
        self._callback_error = threading.Event()
        self._generation = 0
        self._active_generation: int | None = None
        self._stream_token: object | None = None
        self.callback_overflows = 0
        self.callback_status_errors = 0
        self.restart_count = 0

    def _make_callback(self, chunks: queue.Queue[Any], overflowed: threading.Event,
                       callback_error: threading.Event, stream_token: object) -> Callable[..., None]:
        def callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            del frames, time_info
            with self._state_lock:
                generation = self._active_generation
                active_stream = self._stream_token
            if generation is None or active_stream is not stream_token:
                return
            if status:
                self.callback_status_errors += 1
                callback_error.set()
            chunk = indata[:, 0].copy()
            try:
                chunks.put_nowait((generation, chunk))
            except queue.Full:
                self.callback_overflows += 1
                overflowed.set()
        return callback

    def _open(self) -> Any:
        with self._state_lock:
            if self._stream is not None:
                return self._stream
            stream_token = object()
            stream = sd.InputStream(samplerate=self.sample_rate_hz, channels=1, dtype="float32",
                                    device=self.device, callback=self._make_callback(
                                        self._chunks, self._overflowed, self._callback_error,
                                        stream_token))
            self._stream = stream
            self._stream_token = stream_token
            return stream

    def capture(self, timeout_seconds: float) -> tuple[np.ndarray, int]:
        with self._capture_lock:
            with self._state_lock:
                self._generation += 1
                generation = self._generation
                while True:
                    try:
                        self._chunks.get_nowait()
                    except queue.Empty:
                        break
                self._overflowed.clear()
                self._callback_error.clear()
                audio_queue = self._chunks
                overflowed = self._overflowed
                callback_error = self._callback_error
                self._active_generation = generation
            stream = None
            started = False
            capture_error: BaseException | None = None
            deadline = time.monotonic() + timeout_seconds
            chunks: list[np.ndarray] = []
            frame_count = 0
            try:
                stream = self._open()
                stream.start()
                started = True
                while frame_count < self.frames:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"audio capture exceeded {timeout_seconds:.1f}s deadline")
                    try:
                        item = audio_queue.get(timeout=remaining)
                    except queue.Empty as exc:
                        raise TimeoutError(f"audio capture exceeded {timeout_seconds:.1f}s deadline") from exc
                    if item is None:
                        raise InterruptedError("audio capture was closed")
                    item_generation, chunk = item
                    if item_generation != generation:
                        continue
                    if overflowed.is_set():
                        raise RuntimeError("PortAudio callback queue overflowed; capture discarded")
                    if callback_error.is_set():
                        raise RuntimeError("PortAudio reported an input callback status error; capture discarded")
                    chunks.append(chunk)
                    frame_count += len(chunk)
                return np.concatenate(chunks)[:self.frames], self.sample_rate_hz
            except Exception as exc:
                capture_error = exc
                raise
            finally:
                with self._state_lock:
                    if self._active_generation == generation:
                        self._active_generation = None
                stop_error = None
                if started:
                    try:
                        stream.stop()
                    except Exception as exc:
                        stop_error = exc
                if capture_error is not None or stop_error is not None:
                    self._discard_stream(stream)
                chunks.clear()
                if stop_error is not None and capture_error is None:
                    raise RuntimeError(f"Could not stop PortAudio stream: {_short_error(stop_error)}") from stop_error

    def _discard_stream(self, expected_stream: Any) -> None:
        with self._state_lock:
            if self._stream is not expected_stream:
                return
            stream, self._stream = self._stream, None
            self._stream_token = None
        if stream is not None:
            try:
                stream.close()
            except Exception as exc:
                LOGGER.warning("Could not close PortAudio stream: %s", _short_error(exc))

    def close(self) -> None:
        with self._state_lock:
            self._active_generation = None
            stream, self._stream = self._stream, None
            self._stream_token = None
            chunks = self._chunks
            try:
                chunks.put_nowait(None)
            except queue.Full:
                try:
                    chunks.get_nowait()
                    chunks.put_nowait(None)
                except (queue.Empty, queue.Full):
                    pass
        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:
                LOGGER.warning("Could not stop PortAudio stream: %s", _short_error(exc))
            try:
                stream.close()
            except Exception as exc:
                LOGGER.warning("Could not close PortAudio stream: %s", _short_error(exc))


class ArecordCapture(AudioCaptureBackend):
    def __init__(self, record_seconds: int, sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
                 audio_input_device: str | int | None = "default", temp_dir: str = "/tmp") -> None:
        self.record_seconds = record_seconds
        self.sample_rate_hz = sample_rate_hz
        self.device = _arecord_device_name(audio_input_device)
        self.temp_dir = temp_dir
        self._process: subprocess.Popen[bytes] | None = None
        self._sample_path: Path | None = None
        self._capture_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.restart_count = 0

    def capture(self, timeout_seconds: float) -> tuple[np.ndarray, int]:
        with self._capture_lock:
            with NamedTemporaryFile(prefix="fire_audio_monitor_", suffix=".wav", dir=self.temp_dir,
                                    delete=False) as tmp:
                self._sample_path = Path(tmp.name)
            command = build_arecord_command(self.device, self.sample_rate_hz, self.record_seconds, self._sample_path)
            try:
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                with self._state_lock:
                    self._process = process
                try:
                    process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    self._terminate_process(process)
                    raise TimeoutError(f"arecord exceeded {timeout_seconds:.1f}s deadline") from exc
                if process.returncode != 0:
                    raise RuntimeError(f"arecord failed returncode={process.returncode}")
                return read_int16_wav_as_float32(self._sample_path)
            finally:
                with self._state_lock:
                    if self._process is locals().get("process"):
                        self._process = None
                self._remove_sample()

    def _terminate_process(self, process: subprocess.Popen[bytes] | None = None) -> None:
        if process is None:
            with self._state_lock:
                process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            process.wait()
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                LOGGER.error("arecord did not exit after SIGKILL")

    def close(self) -> None:
        with self._state_lock:
            process, self._process = self._process, None
        self._terminate_process(process)

    def _remove_sample(self) -> None:
        path, self._sample_path = self._sample_path, None
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except Exception as exc:
                LOGGER.warning("Could not remove temporary audio sample %s: %s", path, _short_error(exc))


def create_audio_backend(record_seconds: int, sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
                         audio_input_device: str | int | None = "default",
                         audio_capture_backend: str = "sounddevice") -> AudioCaptureBackend:
    backend = audio_capture_backend.strip().lower()
    if backend == "sounddevice":
        return SoundDeviceCapture(record_seconds, sample_rate_hz, audio_input_device)
    if backend == "arecord":
        return ArecordCapture(record_seconds, sample_rate_hz, audio_input_device)
    raise ValueError(f"Unsupported audio_capture_backend {audio_capture_backend!r}")


def capture_audio(
    record_seconds: int,
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
    audio_input_device: str | int | None = "default",
    audio_capture_backend: str = "sounddevice",
) -> tuple[np.ndarray, int]:
    backend = audio_capture_backend.strip().lower()
    LOGGER.debug("Using audio capture backend %s", backend)
    if backend == "arecord":
        return capture_audio_arecord(record_seconds, sample_rate_hz, audio_input_device)
    if backend == "sounddevice":
        return capture_audio_sounddevice(record_seconds, sample_rate_hz, audio_input_device)
    raise ValueError(f"Unsupported audio_capture_backend {audio_capture_backend!r}")


def describe_audio_selection(audio_capture_backend: str, audio_input_device: str | int | None) -> str:
    backend = audio_capture_backend.strip().lower()
    if backend == "sounddevice":
        try:
            resolved = resolve_input_device(audio_input_device)
            return (
                f"backend=sounddevice configured_input={audio_input_device!r} "
                f"resolved_input={resolved if resolved is not None else 'default'}"
            )
        except Exception as exc:
            return (
                f"backend=sounddevice configured_input={audio_input_device!r} "
                f"resolved_input=unavailable error={exc!r}"
            )
    if backend == "arecord":
        return f"backend=arecord configured_input={audio_input_device!r} resolved_input={_arecord_device_name(audio_input_device)}"
    return f"backend={backend!r} configured_input={audio_input_device!r} resolved_input=unsupported-backend"


def capture_audio_sounddevice(
    record_seconds: int,
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
    audio_input_device: str | int | None = "default",
) -> tuple[np.ndarray, int]:
    backend = SoundDeviceCapture(record_seconds, sample_rate_hz, audio_input_device)
    try:
        return backend.capture(record_seconds + 5)
    finally:
        backend.close()


def capture_audio_arecord(
    record_seconds: int,
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
    audio_input_device: str | int | None = "default",
) -> tuple[np.ndarray, int]:
    backend = ArecordCapture(record_seconds, sample_rate_hz, audio_input_device)
    try:
        return backend.capture(record_seconds + 5)
    finally:
        backend.close()


def build_arecord_command(
    audio_input_device: str,
    sample_rate_hz: int,
    record_seconds: int,
    sample_path: Path,
) -> list[str]:
    return [
        "arecord",
        "-D",
        audio_input_device,
        "-f",
        "S16_LE",
        "-r",
        str(sample_rate_hz),
        "-c",
        "1",
        "-d",
        str(record_seconds),
        str(sample_path),
    ]


def read_int16_wav_as_float32(sample_path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(sample_path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate_hz = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())

    if sample_width != 2:
        raise ValueError(f"Expected 16-bit PCM WAV, got sample width {sample_width}")

    int_samples = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        int_samples = int_samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    float_samples = (int_samples.astype(np.float32) / 32768.0).reshape(-1)
    return float_samples, sample_rate_hz


def log_audio_diagnostics() -> None:
    for key in _AUDIO_ENV_KEYS:
        LOGGER.info("Audio env %s=%r", key, os.environ.get(key))

    log_linux_audio_paths()
    log_proc_asound_diagnostics()
    log_audio_command_diagnostics()

    if sd is None:
        LOGGER.warning("sounddevice diagnostics unavailable because sounddevice is not installed")
        return

    try:
        LOGGER.info("sounddevice default device: %r", sd.default.device)
    except Exception as exc:
        LOGGER.warning("Could not query sounddevice default device: %r", exc)

    try:
        LOGGER.info("sounddevice host APIs: %s", sd.query_hostapis())
    except Exception as exc:
        LOGGER.warning("Could not query sounddevice host APIs: %r", exc)

    try:
        devices = sd.query_devices()
        LOGGER.info("sounddevice devices: %s", devices)
        LOGGER.info("Available input devices: %s", format_input_devices(list_input_devices(devices)))
    except Exception as exc:
        LOGGER.warning("Could not query sounddevice devices: %r", exc)


def audio_diagnostics_summary() -> tuple[object, ...]:
    """Log a compact backend summary and return a device-change signature."""
    if sd is None:
        LOGGER.info("Audio summary sounddevice=unavailable")
        return ("unavailable",)
    try:
        default_device = sd.default.device
        devices = list_input_devices(sd.query_devices())
        signature = tuple((device["index"], device["name"], device["max_input_channels"])
                          for device in devices)
        LOGGER.info("Audio summary default_device=%r input_devices=%s",
                    default_device, format_input_devices(devices))
        return (default_device, signature)
    except Exception as exc:
        LOGGER.warning("Could not query compact audio summary: %s", _short_error(exc))
        return ("error", type(exc).__name__, str(exc)[:120])


def list_input_devices(devices: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if devices is None:
        if sd is None:
            raise RuntimeError("sounddevice is not installed")
        devices = sd.query_devices()

    input_devices = []
    for index, device in enumerate(devices):
        max_input_channels = int(device.get("max_input_channels", 0))
        if max_input_channels > 0:
            input_devices.append(
                {
                    "index": int(device.get("index", index)),
                    "name": str(device.get("name", "")),
                    "max_input_channels": max_input_channels,
                }
            )
    return input_devices


def resolve_input_device(
    audio_input_device: str | int | None,
    devices: list[dict[str, Any]] | None = None,
) -> int | str | None:
    if audio_input_device is None:
        LOGGER.info("Using default audio input device")
        return None

    configured = str(audio_input_device).strip()
    if configured == "" or configured.lower() == "default":
        LOGGER.info("Using default audio input device")
        return None

    if _looks_like_alsa_device_string(configured):
        LOGGER.info("Using ALSA-style audio input device string %r", configured)
        return configured

    input_devices = list_input_devices(devices)
    if configured.lstrip("-").isdigit():
        selected_index = int(configured)
        for device in input_devices:
            if device["index"] == selected_index:
                LOGGER.info("Selected audio input device %s: %s", device["index"], device["name"])
                return selected_index
        raise ValueError(
            f"Configured audio input device index {selected_index} is not an available input device"
        )

    configured_lower = configured.lower()
    for device in input_devices:
        if configured_lower in device["name"].lower():
            LOGGER.info("Selected audio input device %s: %s", device["index"], device["name"])
            return int(device["index"])

    raise ValueError(
        f"Configured audio input device {configured!r} did not match an available input device"
    )


def format_input_devices(devices: list[dict[str, Any]]) -> str:
    if not devices:
        return "none"
    return ", ".join(
        f"{device['index']}: {device['name']} ({device['max_input_channels']} input channels)"
        for device in devices
    )


def log_linux_audio_paths() -> None:
    for path in _AUDIO_DEVICE_PATHS:
        try:
            LOGGER.info("Audio path %s exists=%s", path, path.exists())
        except Exception as exc:
            LOGGER.warning("Could not check audio path %s: %r", path, exc)

    snd_path = Path("/dev/snd")
    try:
        if snd_path.exists() and snd_path.is_dir():
            entries = sorted(entry.name for entry in snd_path.iterdir())
            LOGGER.info("/dev/snd entries: %s", entries)
    except Exception as exc:
        LOGGER.warning("Could not list /dev/snd entries: %r", exc)


def log_proc_asound_diagnostics() -> None:
    proc_asound = Path("/proc/asound")
    try:
        LOGGER.info("Audio path %s exists=%s", proc_asound, proc_asound.exists())
    except Exception as exc:
        LOGGER.warning("Could not check audio path %s: %r", proc_asound, exc)

    for path in _PROC_ASOUND_PATHS:
        try:
            if not path.exists():
                LOGGER.info("%s unavailable (expected with some PulseAudio containers)", path)
                continue
            LOGGER.info("%s contents:\n%s", path, path.read_text(encoding="utf-8", errors="replace").strip())
        except Exception as exc:
            LOGGER.warning("Could not read %s: %r", path, exc)


def log_audio_command_diagnostics() -> None:
    for command in (
        ["arecord", "-l"],
        ["arecord", "-L"],
        ["cat", "/proc/asound/cards"],
        ["cat", "/proc/asound/devices"],
    ):
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
            LOGGER.info(
                "Audio diagnostic command %s returncode=%s stdout=%s stderr=%s",
                command,
                result.returncode,
                result.stdout.strip(),
                result.stderr.strip(),
            )
        except Exception as exc:
            LOGGER.warning("Audio diagnostic command %s failed: %r", command, exc)


def _looks_like_alsa_device_string(value: str) -> bool:
    return value.lower().startswith(("hw:", "plughw:"))


def _arecord_device_name(audio_input_device: str | int | None) -> str:
    if audio_input_device is None:
        return "default"
    configured = str(audio_input_device).strip()
    return configured if configured and configured.lower() != "default" else "default"


def _short_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:240]}"
