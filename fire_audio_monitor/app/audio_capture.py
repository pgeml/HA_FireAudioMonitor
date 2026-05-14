from __future__ import annotations

import logging

import numpy as np
import sounddevice as sd


LOGGER = logging.getLogger(__name__)
DEFAULT_SAMPLE_RATE_HZ = 16000


def capture_audio(record_seconds: int, sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ) -> tuple[np.ndarray, int]:
    frames = int(record_seconds * sample_rate_hz)
    LOGGER.debug("Recording %s seconds of audio at %s Hz", record_seconds, sample_rate_hz)
    recording = sd.rec(frames, samplerate=sample_rate_hz, channels=1, dtype="float32")
    sd.wait()
    return recording.reshape(-1), sample_rate_hz
