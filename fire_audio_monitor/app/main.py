from __future__ import annotations

import logging
import time

from audio_capture import capture_audio, format_input_devices, list_input_devices, log_audio_diagnostics
from config import AppConfig, load_config
from detector import DetectionResult, detect_alarm_tone
from ha_client import HomeAssistantClient


LOGGER = logging.getLogger("fire_audio_monitor")


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    config = load_config()
    configure_logging(config.log_level)
    LOGGER.info("Fire Audio Monitor started")
    LOGGER.info("Selected audio capture backend: %s", config.audio_capture_backend)
    LOGGER.info("Configured audio input device: %s", config.audio_input_device)
    log_audio_diagnostics()
    if config.audio_diagnostics_only:
        LOGGER.info("audio_diagnostics_only is enabled; exiting after startup diagnostics")
        return

    client = HomeAssistantClient()
    run_loop(config, client)


def run_loop(config: AppConfig, client: HomeAssistantClient) -> None:
    consecutive_hits = 0
    last_event_at = 0.0

    while True:
        try:
            try:
                samples, sample_rate_hz = capture_audio(
                    config.record_seconds,
                    audio_input_device=config.audio_input_device,
                    audio_capture_backend=config.audio_capture_backend,
                )
            except Exception as exc:
                LOGGER.error(
                    "Audio capture failed for backend=%r configured audio_input_device=%r; "
                    "available input devices=%s; error=%r",
                    config.audio_capture_backend,
                    config.audio_input_device,
                    _available_input_devices_for_log(),
                    exc,
                    exc_info=True,
                )
                time.sleep(config.sample_interval_seconds)
                continue

            result = detect_alarm_tone(
                samples=samples,
                sample_rate_hz=sample_rate_hz,
                min_rms=config.min_rms,
                frequency_min_hz=config.frequency_min_hz,
                frequency_max_hz=config.frequency_max_hz,
            )
            consecutive_hits = consecutive_hits + 1 if result.passed else 0
            LOGGER.debug(
                "Detection result passed=%s rms=%.4f peak=%.1fHz band_ratio=%.3f hits=%s/%s",
                result.passed,
                result.rms,
                result.peak_frequency_hz,
                result.band_ratio,
                consecutive_hits,
                config.required_hits,
            )

            if consecutive_hits >= config.required_hits:
                last_event_at = maybe_fire_event(config, client, result, consecutive_hits, last_event_at)
                consecutive_hits = 0
        except Exception:
            LOGGER.exception("Detection loop failed")

        time.sleep(config.sample_interval_seconds)


def _available_input_devices_for_log() -> str:
    try:
        return format_input_devices(list_input_devices())
    except Exception as exc:
        return f"unavailable ({exc!r})"


def maybe_fire_event(
    config: AppConfig,
    client: HomeAssistantClient,
    result: DetectionResult,
    consecutive_hits: int,
    last_event_at: float,
) -> float:
    now = time.monotonic()
    cooldown_remaining = config.cooldown_seconds - (now - last_event_at)
    if cooldown_remaining > 0:
        LOGGER.info("Detection matched, but cooldown has %.1f seconds remaining", cooldown_remaining)
        return last_event_at

    if config.enable_presence_gate and not client.presence_gate_passes(
        config.presence_entities,
        config.trigger_when_presence_state,
    ):
        LOGGER.info("Detection matched, but presence gate did not pass")
        return last_event_at

    client.fire_event(
        config.ha_event_type,
        {
            "rms": round(result.rms, 6),
            "peak_frequency_hz": round(result.peak_frequency_hz, 2),
            "band_ratio": round(result.band_ratio, 6),
            "required_hits": config.required_hits,
            "observed_hits": consecutive_hits,
        },
    )
    LOGGER.warning("Fire alarm audio pattern detected; Home Assistant event fired")
    return now


if __name__ == "__main__":
    main()
