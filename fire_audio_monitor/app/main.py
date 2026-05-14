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
from alarm_state import AlarmState, AlarmTransition
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
    alarm_state = AlarmState(
        required_hits=config.required_hits,
        clear_hits_required=config.clear_hits_required,
        cooldown_seconds=config.cooldown_seconds,
    )

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
                min_band_ratio=config.min_band_energy_ratio,
            )
            presence_gate_open = get_presence_gate_open(config, client)
            transition = alarm_state.update(
                detected=result.passed,
                presence_gate_open=presence_gate_open,
                now=time.monotonic(),
            )
            if transition.should_fire_event:
                fire_detection_event(config, client, result, transition)
                transition = alarm_state.mark_event_fired(time.monotonic(), transition)

            LOGGER.info(
                "Detector result configured_min_frequency_hz=%s configured_max_frequency_hz=%s "
                "configured_min_rms=%.4f configured_min_band_energy_ratio=%.3f actual_rms=%.4f "
                "actual_dominant_frequency_hz=%.1f actual_band_energy_ratio=%.3f detected=%s "
                "hits=%s required_hits=%s clear_hits=%s required_clear_hits=%s confirmed_detected=%s "
                "presence_gate_open=%s active_alarm=%s cooldown_remaining_seconds=%.1f "
                "event_fired=%s event_status=%s",
                config.frequency_min_hz,
                config.frequency_max_hz,
                config.min_rms,
                config.min_band_energy_ratio,
                result.rms,
                result.peak_frequency_hz,
                result.band_ratio,
                transition.raw_detected,
                transition.hits,
                transition.required_hits,
                transition.clear_hits,
                transition.required_clear_hits,
                transition.confirmed_detected,
                transition.presence_gate_open,
                transition.active_alarm,
                transition.cooldown_remaining_seconds,
                transition.event_fired,
                transition.event_status,
            )
        except Exception:
            LOGGER.exception("Detection loop failed")

        time.sleep(config.sample_interval_seconds)


def _available_input_devices_for_log() -> str:
    try:
        return format_input_devices(list_input_devices())
    except Exception as exc:
        return f"unavailable ({exc!r})"


def get_presence_gate_open(config: AppConfig, client: HomeAssistantClient) -> bool:
    if not config.enable_presence_gate:
        return True
    try:
        return client.presence_gate_passes(
            config.presence_entities,
            config.trigger_when_presence_state,
        )
    except Exception as exc:
        LOGGER.error("Presence gate check failed; treating gate as closed: %r", exc)
        LOGGER.debug("Presence gate traceback", exc_info=True)
        return False


def fire_detection_event(
    config: AppConfig,
    client: HomeAssistantClient,
    result: DetectionResult,
    transition: AlarmTransition,
) -> None:
    client.fire_event(
        config.ha_event_type,
        {
            "rms": round(result.rms, 6),
            "peak_frequency_hz": round(result.peak_frequency_hz, 2),
            "band_ratio": round(result.band_ratio, 6),
            "required_hits": config.required_hits,
            "observed_hits": transition.hits,
        },
    )
    LOGGER.warning("Fire alarm audio pattern detected; Home Assistant event fired")


if __name__ == "__main__":
    main()
