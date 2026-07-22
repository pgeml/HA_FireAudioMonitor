import logging
import gc
import os
import sys
import threading
import time
import tracemalloc
from pathlib import Path
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import AppConfig
from app.detector import DetectionResult
from app.main import FAILED_EVENT, HEARTBEAT_EVENT, RECOVERED_EVENT, MonitorService
from app.runtime_health import LoopWatchdog


class FakeAudio:
    def __init__(self, failures=0):
        self.failures = failures
        self.closed = 0
        self.restarts = 0

    def capture(self, timeout):
        if self.failures:
            self.failures -= 1
            raise TimeoutError("capture")
        return np.zeros(160, dtype=np.float32), 16000

    def restart(self):
        self.restarts += 1
        self.close()

    def close(self):
        self.closed += 1


def config(**values):
    base = dict(record_seconds=1, sample_interval_seconds=1, max_detection_cycle_seconds=10,
                heartbeat_interval_seconds=60, runtime_metrics_interval_seconds=60,
                device_diagnostics_interval_seconds=300, audio_unavailable_failure_seconds=30,
                audio_retry_backoff_seconds=1)
    base.update(values)
    return AppConfig(**base)


def test_health_transitions_and_events_once():
    clock = [0.0]
    client = Mock()
    audio = FakeAudio(failures=3)
    service = MonitorService(config(audio_failure_degraded_threshold=2,
                                    audio_failure_restart_threshold=3), client, audio,
                             monotonic=lambda: clock[0], fatal_exit=lambda code: None)
    service.run_cycle()
    clock[0] += 1
    service.run_cycle()
    assert service.health.state == "degraded"
    clock[0] = 31
    service.run_cycle()
    assert service.health.state == "failed"
    clock[0] += 1
    service.run_cycle()
    assert service.health.state == "healthy"
    event_types = [call.args[0] for call in client.fire_event.call_args_list]
    assert event_types.count(FAILED_EVENT) == 1
    assert event_types.count(RECOVERED_EVENT) == 1
    assert audio.restarts == 1


def test_heartbeat_rate_limited():
    clock = [0.0]
    client = Mock()
    service = MonitorService(config(), client, FakeAudio(), monotonic=lambda: clock[0],
                             fatal_exit=lambda code: None)
    service._periodic()
    service._periodic()
    assert [c.args[0] for c in client.fire_event.call_args_list].count(HEARTBEAT_EVENT) == 1
    clock[0] = 60
    service._periodic()
    assert [c.args[0] for c in client.fire_event.call_args_list].count(HEARTBEAT_EVENT) == 2


def test_shutdown_idempotent():
    audio, client = FakeAudio(), Mock()
    service = MonitorService(config(), client, audio, fatal_exit=lambda code: None)
    service.close()
    service.close()
    assert audio.closed == 1
    client.close.assert_called_once()


def test_signal_request_only_sets_stop_flag():
    audio = FakeAudio()
    service = MonitorService(config(), Mock(), audio, fatal_exit=lambda code: None)
    service.request_stop()
    assert service.stop_event.is_set()
    assert audio.closed == 0
    service.close()


def test_shutdown_coordinator_interrupts_blocked_capture():
    class CancelableAudio(FakeAudio):
        def __init__(self):
            super().__init__()
            self.capture_started = threading.Event()
            self.cancelled = threading.Event()

        def capture(self, timeout):
            self.capture_started.set()
            self.cancelled.wait(timeout)
            raise InterruptedError("closed")

        def close(self):
            self.closed += 1
            self.cancelled.set()

    audio = CancelableAudio()
    client = Mock()
    service = MonitorService(config(), client, audio, fatal_exit=lambda code: None)
    worker = threading.Thread(target=service.run)
    worker.start()
    assert audio.capture_started.wait(1)
    service.request_stop()
    worker.join(timeout=1)
    assert not worker.is_alive()
    client.close.assert_called_once()


def test_detector_failure_eventually_fails_even_when_capture_succeeds(monkeypatch):
    clock = [0.0]
    service = MonitorService(config(audio_failure_degraded_threshold=1), Mock(), FakeAudio(),
                             monotonic=lambda: clock[0], fatal_exit=lambda code: None)
    monkeypatch.setattr("app.main.detect_alarm_tone", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("detector broken")))
    assert not service.run_cycle()
    assert service.health.state == "degraded"
    clock[0] = 31
    assert not service.run_cycle()
    assert service.health.state == "failed"
    assert service.health.consecutive_audio_failures == 0


def test_alarm_event_is_retried_after_delivery_failure(monkeypatch):
    client = Mock()
    client.fire_event.side_effect = [RuntimeError("core down"), None]
    service = MonitorService(config(required_hits=1), client, FakeAudio(), fatal_exit=lambda code: None)
    monkeypatch.setattr("app.main.detect_alarm_tone", lambda *args, **kwargs:
                        DetectionResult(True, 0.2, 3200.0, 0.8))
    assert service.run_cycle()
    assert service._pending_detection is not None
    assert service.run_cycle()
    assert service._pending_detection is None
    assert service.alarm_state.last_event_at > 0


def test_watchdog_fires_for_stall_and_not_for_progress():
    fired = threading.Event()
    progress = [time.monotonic()]
    watchdog = LoopWatchdog(0.15, lambda: progress[0], fired.set)
    watchdog.start()
    assert fired.wait(0.5)
    watchdog.close()

    fired.clear()
    progress[0] = time.monotonic() + 0.2
    watchdog = LoopWatchdog(0.2, lambda: progress[0], fired.set)
    watchdog.start()
    for _ in range(4):
        time.sleep(0.04)
        progress[0] = time.monotonic() + 0.2
    watchdog.close()
    assert not fired.is_set()


def test_watchdog_fatal_exit_is_not_blocked_by_cleanup():
    class BlockingAudio(FakeAudio):
        def close(self):
            threading.Event().wait(5)

    exited = threading.Event()
    service = MonitorService(config(), Mock(), BlockingAudio(), fatal_exit=lambda code: exited.set())
    started = time.monotonic()
    service._watchdog_stalled()
    assert exited.is_set()
    assert time.monotonic() - started < 2.5


def test_repeated_errors_are_rate_limited(caplog):
    service = MonitorService(config(), Mock(), FakeAudio(failures=5), fatal_exit=lambda code: None)
    with caplog.at_level(logging.ERROR):
        for _ in range(5):
            service.run_cycle()
    assert sum("audio failure" in r.message for r in caplog.records) == 1


def test_thousands_of_cycles_do_not_grow_owned_resources(tmp_path):
    service = MonitorService(config(), Mock(), FakeAudio(), fatal_exit=lambda code: None)
    before_threads = threading.active_count()
    fd_path = "/dev/fd"
    before_fds = len(os.listdir(fd_path)) if os.path.isdir(fd_path) else None
    for _ in range(20):
        service.run_cycle()
    gc.collect()
    tracemalloc.start()
    before_memory = tracemalloc.get_traced_memory()[0]
    for _ in range(2000):
        assert service.run_cycle()
    gc.collect()
    after_memory = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    assert threading.active_count() == before_threads
    if before_fds is not None:
        assert len(os.listdir(fd_path)) <= before_fds + 1
    assert after_memory - before_memory < 1_000_000
    assert not list(tmp_path.glob("fire_audio_monitor_*.wav"))
    service.close()
