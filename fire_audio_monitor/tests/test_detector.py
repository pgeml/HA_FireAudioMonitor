import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.detector import detect_alarm_tone


def sine_wave(frequency_hz: int, seconds: float = 1.0, sample_rate_hz: int = 16000, amplitude: float = 0.2):
    times = np.arange(int(seconds * sample_rate_hz)) / sample_rate_hz
    return amplitude * np.sin(2 * np.pi * frequency_hz * times)


def test_detects_tone_inside_configured_band():
    samples = sine_wave(3200)

    result = detect_alarm_tone(
        samples=samples,
        sample_rate_hz=16000,
        min_rms=0.01,
        frequency_min_hz=3000,
        frequency_max_hz=3400,
    )

    assert result.passed is True
    assert 3190 <= result.peak_frequency_hz <= 3210
    assert result.rms > 0.01


def test_rejects_tone_outside_configured_band():
    samples = sine_wave(1000)

    result = detect_alarm_tone(
        samples=samples,
        sample_rate_hz=16000,
        min_rms=0.01,
        frequency_min_hz=3000,
        frequency_max_hz=3400,
    )

    assert result.passed is False


def test_rejects_silence():
    samples = np.zeros(16000)

    result = detect_alarm_tone(
        samples=samples,
        sample_rate_hz=16000,
        min_rms=0.01,
        frequency_min_hz=3000,
        frequency_max_hz=4000,
    )

    assert result.passed is False
    assert result.rms == 0.0


def test_rejects_low_rms_even_when_frequency_matches():
    samples = sine_wave(3200, amplitude=0.001)

    result = detect_alarm_tone(
        samples=samples,
        sample_rate_hz=16000,
        min_rms=0.01,
        frequency_min_hz=3000,
        frequency_max_hz=3400,
    )

    assert result.passed is False
    assert result.rms < 0.01
