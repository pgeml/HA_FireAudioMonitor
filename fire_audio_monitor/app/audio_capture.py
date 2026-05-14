from __future__ import annotations

import logging
import os
import subprocess
import wave
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

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
)


def capture_audio(
    record_seconds: int,
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
    audio_input_device: str | int | None = "default",
    audio_capture_backend: str = "arecord",
) -> tuple[np.ndarray, int]:
    backend = audio_capture_backend.strip().lower()
    LOGGER.debug("Using audio capture backend %s", backend)
    if backend == "arecord":
        return capture_audio_arecord(record_seconds, sample_rate_hz, audio_input_device)
    if backend == "sounddevice":
        return capture_audio_sounddevice(record_seconds, sample_rate_hz, audio_input_device)
    raise ValueError(f"Unsupported audio_capture_backend {audio_capture_backend!r}")


def capture_audio_sounddevice(
    record_seconds: int,
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
    audio_input_device: str | int | None = "default",
) -> tuple[np.ndarray, int]:
    if sd is None:
        raise RuntimeError("sounddevice is not installed")

    frames = int(record_seconds * sample_rate_hz)
    selected_device = resolve_input_device(audio_input_device)
    LOGGER.debug(
        "Recording %s seconds of audio at %s Hz using input device %r",
        record_seconds,
        sample_rate_hz,
        selected_device if selected_device is not None else "default",
    )
    recording = sd.rec(
        frames,
        samplerate=sample_rate_hz,
        channels=1,
        dtype="float32",
        device=selected_device,
    )
    sd.wait()
    return recording.reshape(-1), sample_rate_hz


def capture_audio_arecord(
    record_seconds: int,
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
    audio_input_device: str | int | None = "plughw:1,0",
) -> tuple[np.ndarray, int]:
    selected_device = _arecord_device_name(audio_input_device)
    with NamedTemporaryFile(prefix="fire_audio_monitor_", suffix=".wav", dir="/tmp", delete=False) as tmp:
        sample_path = Path(tmp.name)

    command = build_arecord_command(selected_device, sample_rate_hz, record_seconds, sample_path)
    LOGGER.debug("Recording audio with arecord device=%r command=%s", selected_device, command)
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=record_seconds + 5,
        )
        if result.returncode != 0:
            LOGGER.error("arecord failed returncode=%s stderr=%s", result.returncode, result.stderr.strip())
            raise RuntimeError(f"arecord failed with return code {result.returncode}: {result.stderr.strip()}")

        samples, wav_sample_rate_hz = read_int16_wav_as_float32(sample_path)
        if wav_sample_rate_hz != sample_rate_hz:
            LOGGER.warning("arecord WAV sample rate was %s Hz, expected %s Hz", wav_sample_rate_hz, sample_rate_hz)
        return samples, wav_sample_rate_hz
    finally:
        try:
            sample_path.unlink(missing_ok=True)
        except TypeError:  # Python < 3.8 compatibility guard.
            if sample_path.exists():
                sample_path.unlink()
        except Exception as exc:
            LOGGER.warning("Could not remove temporary audio sample %s: %r", sample_path, exc)


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
    if sd is None:
        LOGGER.warning("Audio diagnostics unavailable because sounddevice is not installed")
        return

    for key in _AUDIO_ENV_KEYS:
        LOGGER.info("Audio env %s=%r", key, os.environ.get(key))

    log_linux_audio_paths()

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


def _looks_like_alsa_device_string(value: str) -> bool:
    return value.lower().startswith(("hw:", "plughw:"))


def _arecord_device_name(audio_input_device: str | int | None) -> str:
    if audio_input_device is None:
        return "default"
    configured = str(audio_input_device).strip()
    return configured if configured and configured.lower() != "default" else "default"
