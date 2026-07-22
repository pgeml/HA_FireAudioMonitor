from __future__ import annotations

import logging
import os
import signal
import threading
import time
from typing import Callable

try:  # Support both /app execution and package imports in tests.
    from .alarm_state import AlarmState, AlarmTransition
    from .audio_capture import (AudioCaptureBackend, create_audio_backend, describe_audio_selection,
                                log_audio_diagnostics)
    from .config import AppConfig, load_config
    from .detector import DetectionResult, detect_alarm_tone
    from .ha_client import HomeAssistantClient
    from .runtime_health import LoopWatchdog, RuntimeHealth, resource_metrics, utc_now
except ImportError:  # pragma: no cover - add-on executes main.py directly.
    from alarm_state import AlarmState, AlarmTransition
    from audio_capture import (AudioCaptureBackend, create_audio_backend, describe_audio_selection,
                               log_audio_diagnostics)
    from config import AppConfig, load_config
    from detector import DetectionResult, detect_alarm_tone
    from ha_client import HomeAssistantClient
    from runtime_health import LoopWatchdog, RuntimeHealth, resource_metrics, utc_now


LOGGER = logging.getLogger("fire_audio_monitor")
HEARTBEAT_EVENT = "fire_audio_monitor_heartbeat"
HEALTH_CHANGED_EVENT = "fire_audio_monitor_health_changed"
FAILED_EVENT = "fire_audio_monitor_failed"
RECOVERED_EVENT = "fire_audio_monitor_recovered"


def configure_logging(level_name: str) -> None:
    logging.basicConfig(level=getattr(logging, level_name.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class ErrorRateLimiter:
    def __init__(self, interval: float = 300.0) -> None:
        self.interval = interval
        self._seen: dict[str, tuple[float, int]] = {}

    def should_log(self, key: str, now: float) -> tuple[bool, int]:
        if key not in self._seen:
            self._seen[key] = (now, 0)
            return True, 0
        last, suppressed = self._seen.get(key, (0.0, 0))
        if now - last >= self.interval:
            self._seen[key] = (now, 0)
            return True, suppressed
        self._seen[key] = (last, suppressed + 1)
        return False, 0


class MonitorService:
    def __init__(self, config: AppConfig, client: HomeAssistantClient,
                 audio: AudioCaptureBackend | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 fatal_exit: Callable[[int], None] = os._exit) -> None:
        self.config = config
        self.client = client
        self.audio = audio or create_audio_backend(config.record_seconds,
                                                    audio_input_device=config.audio_input_device,
                                                    audio_capture_backend=config.audio_capture_backend)
        self.monotonic = monotonic
        self.fatal_exit = fatal_exit
        self.health = RuntimeHealth(started_monotonic=monotonic())
        self.alarm_state = AlarmState(config.required_hits, config.clear_hits_required, config.cooldown_seconds)
        self.stop_event = threading.Event()
        self._closed = False
        started = monotonic()
        self._progress_deadline = started + config.max_detection_cycle_seconds
        self._next_heartbeat = started
        self._next_metrics = started
        self._next_diagnostics = started + config.device_diagnostics_interval_seconds
        self._pending_detection: tuple[dict[str, object], AlarmTransition] | None = None
        self._shutdown_thread: threading.Thread | None = None
        self._errors = ErrorRateLimiter()
        self.watchdog = LoopWatchdog(config.max_detection_cycle_seconds,
                                     lambda: self._progress_deadline, self._watchdog_stalled)

    def run(self) -> None:
        self.watchdog.start()
        self._shutdown_thread = threading.Thread(
            target=self._shutdown_audio_when_requested, name="shutdown-coordinator", daemon=True
        )
        self._shutdown_thread.start()
        try:
            while not self.stop_event.is_set():
                self._progress_deadline = self.monotonic() + self.config.max_detection_cycle_seconds
                self.run_cycle()
                self._periodic()
                if self.health.state == "failed":
                    raise RuntimeError("audio monitoring remained unavailable beyond configured threshold")
                delay = self.config.audio_retry_backoff_seconds if self.health.consecutive_audio_failures else self.config.sample_interval_seconds
                self._progress_deadline = self.monotonic() + delay + self.config.max_detection_cycle_seconds
                self.stop_event.wait(delay)
        finally:
            self.close()

    def _shutdown_audio_when_requested(self) -> None:
        self.stop_event.wait()
        self.audio.close()

    def run_cycle(self) -> bool:
        now = self.monotonic()
        try:
            samples, sample_rate_hz = self.audio.capture(self.config.record_seconds + 5.0)
            self.health.audio_ok(self.monotonic())
        except Exception as exc:
            message = _short_error(exc)
            self.health.audio_failed("audio_capture", message)
            if self.health.consecutive_audio_failures % self.config.audio_failure_restart_threshold == 0:
                self.audio.restart()
                self.health.stream_restarts += 1
                log_restart, suppressed = self._errors.should_log("audio_backend_restart", now)
                if log_restart:
                    LOGGER.warning("Audio backend restarted consecutive_failures=%s restarts=%s "
                                   "suppressed_similar=%s", self.health.consecutive_audio_failures,
                                   self.health.stream_restarts, suppressed)
            self._log_recurring_error("audio", message, now)
            self._update_health(complete_success=False)
            return False

        try:
            result = detect_alarm_tone(samples, sample_rate_hz, self.config.min_rms,
                                       self.config.frequency_min_hz, self.config.frequency_max_hz,
                                       self.config.min_band_energy_ratio)
            gate_open = self._presence_gate()
            transition = self.alarm_state.update(result.passed, gate_open, self.monotonic())
            if transition.should_fire_event:
                self._pending_detection = (self._detection_payload(result, transition), transition)
            if self._pending_detection is not None:
                payload, pending_transition = self._pending_detection
                if self._send_event(self.config.ha_event_type, payload):
                    transition = self.alarm_state.mark_event_fired(self.monotonic(), pending_transition)
                    self._pending_detection = None
                    LOGGER.warning("Fire alarm audio pattern detected; Home Assistant event fired")
            self.health.cycle_ok(self.monotonic())
            self._update_health(complete_success=True)
            LOGGER.debug("Detector result actual_rms=%.4f actual_dominant_frequency_hz=%.1f "
                         "actual_band_energy_ratio=%.3f detected=%s hits=%s clear_hits=%s "
                         "active_alarm=%s event_status=%s", result.rms, result.peak_frequency_hz,
                         result.band_ratio, transition.raw_detected, transition.hits,
                         transition.clear_hits, transition.active_alarm, transition.event_status)
            return True
        except Exception as exc:
            message = _short_error(exc)
            self.health.detection_failed("detection_cycle", message)
            self._log_recurring_error("detection", message, now)
            self._update_health(complete_success=False)
            return False
        finally:
            del samples

    def _log_recurring_error(self, category: str, message: str, now: float) -> None:
        log, suppressed = self._errors.should_log(f"{category}:{message}", now)
        if log:
            LOGGER.error("%s failure error=%s suppressed_similar=%s", category, message, suppressed)

    def _presence_gate(self) -> bool:
        if not self.config.enable_presence_gate:
            return True
        try:
            value = self.client.presence_gate_passes(self.config.presence_entities,
                                                     self.config.trigger_when_presence_state)
            self.health.ha_ok(self.monotonic())
            return value
        except Exception as exc:
            self.health.ha_failed(_short_error(exc))
            self._log_recurring_error("presence_gate", _short_error(exc), self.monotonic())
            return False

    def _send_event(self, event_type: str, payload: dict[str, object]) -> bool:
        try:
            self.client.fire_event(event_type, payload)
            self.health.ha_ok(self.monotonic(), event_delivery=True)
            return True
        except Exception as exc:
            self.health.ha_failed(_short_error(exc))
            self._log_recurring_error(f"ha_event:{event_type}", _short_error(exc), self.monotonic())
            return False

    def _detection_payload(self, result: DetectionResult, transition: AlarmTransition) -> dict[str, object]:
        return {
            "rms": round(result.rms, 6), "peak_frequency_hz": round(result.peak_frequency_hz, 2),
            "band_ratio": round(result.band_ratio, 6), "required_hits": self.config.required_hits,
            "observed_hits": transition.hits, "detected_at": utc_now(),
        }

    def _update_health(self, complete_success: bool) -> None:
        now = self.monotonic()
        new_state = self.health.evaluate(now, self.config.audio_failure_degraded_threshold,
                                         self.config.audio_unavailable_failure_seconds)
        old_state = self.health.state
        if new_state == old_state:
            return
        self.health.state = new_state
        payload = self.health.compact(now) | {"previous_health": old_state, "timestamp": utc_now()}
        self._send_event(HEALTH_CHANGED_EVENT, payload)
        if new_state == "failed":
            self._send_event(FAILED_EVENT, payload)
        elif new_state == "healthy" and complete_success and old_state in {"degraded", "failed"}:
            self._send_event(RECOVERED_EVENT, payload)
        LOGGER.warning("Monitoring health changed previous=%s current=%s", old_state, new_state)

    def _periodic(self) -> None:
        now = self.monotonic()
        self._update_health(complete_success=False)
        if now >= self._next_heartbeat:
            self._send_event(HEARTBEAT_EVENT, self.health.compact(now) | {"timestamp": utc_now()})
            self._next_heartbeat = now + self.config.heartbeat_interval_seconds
        if now >= self._next_metrics:
            try:
                metrics = resource_metrics(self.health, now)
                LOGGER.info("Runtime metrics %s", " ".join(f"{key}={value}" for key, value in metrics.items()))
            except Exception as exc:
                self._log_recurring_error("runtime_metrics", _short_error(exc), now)
            self._next_metrics = now + self.config.runtime_metrics_interval_seconds
        if now >= self._next_diagnostics:
            try:
                log_audio_diagnostics()
            except Exception as exc:
                self._log_recurring_error("audio_diagnostics", _short_error(exc), now)
            self._next_diagnostics = now + self.config.device_diagnostics_interval_seconds

    def _watchdog_stalled(self) -> None:
        if self.stop_event.is_set():
            return
        LOGGER.critical("Detection loop stalled max_detection_cycle_seconds=%s; closing audio backend and exiting",
                        self.config.max_detection_cycle_seconds)
        cleanup = threading.Thread(target=self.audio.close, name="watchdog-audio-cleanup", daemon=True)
        cleanup.start()
        cleanup.join(timeout=2)
        self.fatal_exit(2)

    def request_stop(self) -> None:
        self.stop_event.set()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop_event.set()
        try:
            self.watchdog.close()
        finally:
            try:
                if (self._shutdown_thread is not None and
                        self._shutdown_thread is not threading.current_thread()):
                    self._shutdown_thread.join(timeout=2)
                self.audio.close()
            finally:
                self.client.close()
        LOGGER.info("Fire Audio Monitor shutdown complete")


def main() -> int:
    service: MonitorService | None = None
    try:
        config = load_config()
        configure_logging(config.log_level)
        LOGGER.info("Fire Audio Monitor started")
        LOGGER.info("Audio selection: %s", describe_audio_selection(config.audio_capture_backend,
                                                                     config.audio_input_device))
        if config.audio_diagnostics_only or config.audio_diagnostics_on_startup:
            log_audio_diagnostics()
        if config.audio_diagnostics_only:
            LOGGER.info("audio_diagnostics_only enabled; exiting")
            return 0
        service = MonitorService(config, HomeAssistantClient())
        signal.signal(signal.SIGTERM, lambda *_: service.request_stop())
        signal.signal(signal.SIGINT, lambda *_: service.request_stop())
        service.run()
        return 0
    except Exception:
        LOGGER.exception("Fatal initialization or runtime failure")
        return 1
    finally:
        if service is not None:
            service.close()


def _short_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:240]}"


if __name__ == "__main__":
    raise SystemExit(main())
