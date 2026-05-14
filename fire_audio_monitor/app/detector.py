from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DetectionResult:
    passed: bool
    rms: float
    peak_frequency_hz: float
    band_ratio: float


def detect_alarm_tone(
    samples: np.ndarray,
    sample_rate_hz: int,
    min_rms: float,
    frequency_min_hz: int,
    frequency_max_hz: int,
    min_band_ratio: float = 0.35,
) -> DetectionResult:
    """Return a deterministic RMS and FFT decision for mono audio samples."""
    mono = _as_mono_float(samples)
    if mono.size == 0:
        return DetectionResult(False, 0.0, 0.0, 0.0)

    rms = float(np.sqrt(np.mean(np.square(mono))))
    if rms < min_rms:
        return DetectionResult(False, rms, 0.0, 0.0)

    windowed = mono * np.hanning(mono.size)
    magnitudes = np.abs(np.fft.rfft(windowed))
    frequencies = np.fft.rfftfreq(mono.size, d=1.0 / sample_rate_hz)
    total_energy = float(np.sum(magnitudes))
    if total_energy <= 0:
        return DetectionResult(False, rms, 0.0, 0.0)

    band_mask = (frequencies >= frequency_min_hz) & (frequencies <= frequency_max_hz)
    if not np.any(band_mask):
        return DetectionResult(False, rms, 0.0, 0.0)

    band_magnitudes = magnitudes[band_mask]
    band_frequencies = frequencies[band_mask]
    peak_index = int(np.argmax(band_magnitudes))
    peak_frequency_hz = float(band_frequencies[peak_index])
    band_ratio = float(np.sum(band_magnitudes) / total_energy)

    return DetectionResult(
        passed=band_ratio >= min_band_ratio,
        rms=rms,
        peak_frequency_hz=peak_frequency_hz,
        band_ratio=band_ratio,
    )


def _as_mono_float(samples: np.ndarray) -> np.ndarray:
    array = np.asarray(samples, dtype=np.float64)
    if array.ndim == 2:
        array = np.mean(array, axis=1)
    return array.reshape(-1)
