from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)
OPTIONS_PATH = Path("/data/options.json")


@dataclass(frozen=True)
class AppConfig:
    log_level: str = "info"
    sample_interval_seconds: int = 5
    record_seconds: int = 3
    audio_capture_backend: str = "sounddevice"
    audio_input_device: str = "pulse"
    audio_diagnostics_only: bool = False
    audio_diagnostics_on_startup: bool = False
    min_rms: float = 0.02
    min_band_energy_ratio: float = 0.35
    frequency_min_hz: int = 3000
    frequency_max_hz: int = 4000
    required_hits: int = 2
    clear_hits_required: int = 2
    cooldown_seconds: int = 60
    enable_presence_gate: bool = False
    presence_entities: tuple[str, ...] = ()
    trigger_when_presence_state: str = "on"
    ha_event_type: str = "fire_audio_monitor_detected"
    heartbeat_interval_seconds: int = 300
    runtime_metrics_interval_seconds: int = 600
    audio_failure_degraded_threshold: int = 3
    audio_failure_restart_threshold: int = 5
    rolling_capture_window: int = 20
    audio_failure_ratio_degraded_threshold: float = 0.1
    health_recovery_clean_cycles: int = 3
    audio_unavailable_failure_seconds: int = 600
    max_detection_cycle_seconds: int = 180
    audio_retry_backoff_seconds: int = 5
    device_diagnostics_interval_seconds: int = 3600


def load_config(path: Path = OPTIONS_PATH) -> AppConfig:
    if not path.exists():
        LOGGER.warning("Options file %s not found; using defaults", path)
        raw: dict[str, Any] = {}
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))

    config = AppConfig(
        log_level=str(raw.get("log_level", AppConfig.log_level)).lower(),
        sample_interval_seconds=int(raw.get("sample_interval_seconds", AppConfig.sample_interval_seconds)),
        record_seconds=int(raw.get("record_seconds", AppConfig.record_seconds)),
        audio_capture_backend=str(raw.get("audio_capture_backend", AppConfig.audio_capture_backend)).lower(),
        audio_input_device=str(raw.get("audio_input_device", AppConfig.audio_input_device)),
        audio_diagnostics_only=bool(raw.get("audio_diagnostics_only", AppConfig.audio_diagnostics_only)),
        audio_diagnostics_on_startup=bool(
            raw.get("audio_diagnostics_on_startup", AppConfig.audio_diagnostics_on_startup)
        ),
        min_rms=float(raw.get("min_rms", AppConfig.min_rms)),
        min_band_energy_ratio=float(raw.get("min_band_energy_ratio", AppConfig.min_band_energy_ratio)),
        frequency_min_hz=int(raw.get("frequency_min_hz", AppConfig.frequency_min_hz)),
        frequency_max_hz=int(raw.get("frequency_max_hz", AppConfig.frequency_max_hz)),
        required_hits=int(raw.get("required_hits", AppConfig.required_hits)),
        clear_hits_required=int(raw.get("clear_hits_required", AppConfig.clear_hits_required)),
        cooldown_seconds=int(raw.get("cooldown_seconds", AppConfig.cooldown_seconds)),
        enable_presence_gate=bool(raw.get("enable_presence_gate", AppConfig.enable_presence_gate)),
        presence_entities=tuple(str(entity) for entity in raw.get("presence_entities", ())),
        trigger_when_presence_state=str(
            raw.get("trigger_when_presence_state", AppConfig.trigger_when_presence_state)
        ),
        ha_event_type=str(raw.get("ha_event_type", AppConfig.ha_event_type)),
        heartbeat_interval_seconds=int(raw.get("heartbeat_interval_seconds", AppConfig.heartbeat_interval_seconds)),
        runtime_metrics_interval_seconds=int(raw.get("runtime_metrics_interval_seconds", AppConfig.runtime_metrics_interval_seconds)),
        audio_failure_degraded_threshold=int(raw.get("audio_failure_degraded_threshold", AppConfig.audio_failure_degraded_threshold)),
        audio_failure_restart_threshold=int(raw.get("audio_failure_restart_threshold", AppConfig.audio_failure_restart_threshold)),
        rolling_capture_window=int(raw.get("rolling_capture_window", AppConfig.rolling_capture_window)),
        audio_failure_ratio_degraded_threshold=float(raw.get(
            "audio_failure_ratio_degraded_threshold", AppConfig.audio_failure_ratio_degraded_threshold)),
        health_recovery_clean_cycles=int(raw.get(
            "health_recovery_clean_cycles", AppConfig.health_recovery_clean_cycles)),
        audio_unavailable_failure_seconds=int(raw.get("audio_unavailable_failure_seconds", AppConfig.audio_unavailable_failure_seconds)),
        max_detection_cycle_seconds=int(raw.get("max_detection_cycle_seconds", AppConfig.max_detection_cycle_seconds)),
        audio_retry_backoff_seconds=int(raw.get("audio_retry_backoff_seconds", AppConfig.audio_retry_backoff_seconds)),
        device_diagnostics_interval_seconds=int(raw.get("device_diagnostics_interval_seconds", AppConfig.device_diagnostics_interval_seconds)),
    )
    validate_config(config)
    return config


def validate_config(config: AppConfig) -> None:
    if config.frequency_min_hz >= config.frequency_max_hz:
        raise ValueError("frequency_min_hz must be lower than frequency_max_hz")
    if config.sample_interval_seconds <= 0:
        raise ValueError("sample_interval_seconds must be positive")
    if config.record_seconds <= 0:
        raise ValueError("record_seconds must be positive")
    if config.audio_capture_backend not in {"sounddevice", "arecord"}:
        raise ValueError("audio_capture_backend must be sounddevice or arecord")
    if config.required_hits <= 0:
        raise ValueError("required_hits must be positive")
    if config.clear_hits_required <= 0:
        raise ValueError("clear_hits_required must be positive")
    if not 0 <= config.min_rms <= 1:
        raise ValueError("min_rms must be between 0 and 1")
    if not 0 <= config.min_band_energy_ratio <= 1:
        raise ValueError("min_band_energy_ratio must be between 0 and 1")
    if config.enable_presence_gate and not config.presence_entities:
        raise ValueError("presence_entities must be set when enable_presence_gate is true")
    intervals = {
        "heartbeat_interval_seconds": (config.heartbeat_interval_seconds, 60, 86400),
        "runtime_metrics_interval_seconds": (config.runtime_metrics_interval_seconds, 60, 86400),
        "audio_unavailable_failure_seconds": (config.audio_unavailable_failure_seconds, 30, 86400),
        "max_detection_cycle_seconds": (config.max_detection_cycle_seconds, 2, 3600),
        "audio_retry_backoff_seconds": (config.audio_retry_backoff_seconds, 1, 300),
        "device_diagnostics_interval_seconds": (config.device_diagnostics_interval_seconds, 300, 604800),
    }
    for name, (value, minimum, maximum) in intervals.items():
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
    # Capture can use record+5 seconds. A periodic diagnostics pass can use four
    # 10-second subprocess deadlines. Reserve four HA API calls plus one per
    # configured presence entity at the rounded 14-second request deadline.
    minimum_cycle_seconds = (
        config.record_seconds + 5 + 40 + 10 + 14 * (4 + len(config.presence_entities))
    )
    if config.max_detection_cycle_seconds < minimum_cycle_seconds:
        raise ValueError(
            "max_detection_cycle_seconds must be at least "
            f"{minimum_cycle_seconds} for the configured capture and API operations"
        )
    if config.audio_failure_degraded_threshold < 1:
        raise ValueError("audio_failure_degraded_threshold must be positive")
    if config.audio_failure_restart_threshold < config.audio_failure_degraded_threshold:
        raise ValueError("audio_failure_restart_threshold must be greater than or equal to audio_failure_degraded_threshold")
    if config.audio_failure_restart_threshold > 100:
        raise ValueError("audio_failure_restart_threshold must not exceed 100")
    if not 1 <= config.rolling_capture_window <= 1000:
        raise ValueError("rolling_capture_window must be between 1 and 1000")
    if not 0 < config.audio_failure_ratio_degraded_threshold <= 1:
        raise ValueError("audio_failure_ratio_degraded_threshold must be greater than 0 and at most 1")
    if config.health_recovery_clean_cycles < 1:
        raise ValueError("health_recovery_clean_cycles must be positive")
