from __future__ import annotations

import logging
import time

from audio_capture import (
    capture_audio,
    describe_audio_selection,
    format_input_devices,
    list_input_devices,
    log_audio_diagnostics,
)
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
    LOGGER.info("Audio selection: %s", describe_audio_selection(config.audio_capture_backend, config.audio_input_device))
    if config.audio_diagnostics_only or config.audio_diagnostics_on_startup:
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
                )
                LOGGER.debug("Audio capture traceback", exc_info=True)
                time.sleep(config.sample_interval_seconds)
                continue

            LOGGER.info(
                "Audio capture succeeded backend=%s input_device=%s sample_rate_hz=%s samples=%s record_seconds=%s",
                config.audio_capture_backend,
                config.audio_input_device,
                sample_rate_hz,
                len(samples),
                config.record_seconds,
            )
            result = detect_alarm_tone(
                samples=samples,
                sample_rate_hz=sample_rate_hz,
                min_rms=config.min_rms,
                frequency_min_hz=config.frequency_min_hz,
                frequency_max_hz=config.frequency_max_hz,
            )
            consecutive_hits = consecutive_hits + 1 if result.passed else 0
            observed_hits = consecutive_hits
            event_fired = False
            event_status = "not-fired"

            if observed_hits >= config.required_hits:
                last_event_at, event_fired, event_status = maybe_fire_event(
                    config,
                    client,
                    result,
                    observed_hits,
                    last_event_at,
                )
                consecutive_hits = 0
            elif result.passed:
                event_status = "waiting-for-required-hits"
            else:
                event_status = "detector-not-matched"

            LOGGER.info(
                "Detector result rms=%.4f dominant_frequency_hz=%.1f band_energy_ratio=%.3f "
                "detected=%s hits=%s/%s event_fired=%s event_status=%s",
                result.rms,
                result.peak_frequency_hz,
                result.band_ratio,
                result.passed,
                observed_hits,
                config.required_hits,
                event_fired,
                event_status,
            )
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
) -> tuple[float, bool, str]:
    now = time.monotonic()
    cooldown_remaining = config.cooldown_seconds - (now - last_event_at)
    if cooldown_remaining > 0:
        LOGGER.info("Detection matched, but cooldown has %.1f seconds remaining", cooldown_remaining)
        return last_event_at, False, "cooldown"

    if config.enable_presence_gate and not client.presence_gate_passes(
        config.presence_entities,
        config.trigger_when_presence_state,
    ):
        LOGGER.info("Detection matched, but presence gate did not pass")
        return last_event_at, False, "presence-gate-blocked"

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
    return now, True, "event-fired"


if __name__ == "__main__":
    main()
