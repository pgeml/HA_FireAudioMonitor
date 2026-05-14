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
    audio_input_device: str = "default"
    min_rms: float = 0.02
    frequency_min_hz: int = 3000
    frequency_max_hz: int = 4000
    required_hits: int = 2
    cooldown_seconds: int = 60
    enable_presence_gate: bool = False
    presence_entities: tuple[str, ...] = ()
    trigger_when_presence_state: str = "on"
    ha_event_type: str = "fire_audio_monitor_detected"


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
        audio_input_device=str(raw.get("audio_input_device", AppConfig.audio_input_device)),
        min_rms=float(raw.get("min_rms", AppConfig.min_rms)),
        frequency_min_hz=int(raw.get("frequency_min_hz", AppConfig.frequency_min_hz)),
        frequency_max_hz=int(raw.get("frequency_max_hz", AppConfig.frequency_max_hz)),
        required_hits=int(raw.get("required_hits", AppConfig.required_hits)),
        cooldown_seconds=int(raw.get("cooldown_seconds", AppConfig.cooldown_seconds)),
        enable_presence_gate=bool(raw.get("enable_presence_gate", AppConfig.enable_presence_gate)),
        presence_entities=tuple(str(entity) for entity in raw.get("presence_entities", ())),
        trigger_when_presence_state=str(
            raw.get("trigger_when_presence_state", AppConfig.trigger_when_presence_state)
        ),
        ha_event_type=str(raw.get("ha_event_type", AppConfig.ha_event_type)),
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
    if config.required_hits <= 0:
        raise ValueError("required_hits must be positive")
    if not 0 <= config.min_rms <= 1:
        raise ValueError("min_rms must be between 0 and 1")
    if config.enable_presence_gate and not config.presence_entities:
        raise ValueError("presence_entities must be set when enable_presence_gate is true")
