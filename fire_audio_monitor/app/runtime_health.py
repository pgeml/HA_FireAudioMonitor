from __future__ import annotations

import glob
import os
import resource
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RuntimeHealth:
    started_monotonic: float = field(default_factory=time.monotonic)
    started_at: str = field(default_factory=utc_now)
    last_audio_success: float | None = None
    last_cycle_completed: float | None = None
    last_ha_success: float | None = None
    last_event_delivery: float | None = None
    consecutive_audio_failures: int = 0
    total_audio_failures: int = 0
    consecutive_detection_failures: int = 0
    total_detection_failures: int = 0
    stream_restarts: int = 0
    consecutive_ha_failures: int = 0
    total_ha_failures: int = 0
    total_cycles: int = 0
    capture_attempts: int = 0
    successful_captures: int = 0
    consecutive_clean_cycles: int = 0
    rolling_capture_window_size: int = 20
    last_error_category: str = "none"
    last_error_message: str = ""
    state: str = "healthy"
    ha_state: str = "healthy"
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _capture_results: deque[bool] = field(default_factory=deque, repr=False)
    _callback_overflow_results: deque[bool] = field(default_factory=deque, repr=False)

    def __post_init__(self) -> None:
        self._capture_results = deque(maxlen=self.rolling_capture_window_size)
        self._callback_overflow_results = deque(maxlen=self.rolling_capture_window_size)

    def audio_ok(self, now: float) -> None:
        with self._lock:
            self.last_audio_success = now
            self.consecutive_audio_failures = 0
            self.capture_attempts += 1
            self.successful_captures += 1
            self._capture_results.append(True)
            self._callback_overflow_results.append(False)

    def audio_failed(self, category: str, message: str) -> None:
        with self._lock:
            self.consecutive_audio_failures += 1
            self.total_audio_failures += 1
            self.capture_attempts += 1
            self.consecutive_clean_cycles = 0
            self._capture_results.append(False)
            self._callback_overflow_results.append("callback queue overflowed" in message.lower())
            self.last_error_category = category
            self.last_error_message = message[:240]

    def cycle_ok(self, now: float) -> None:
        with self._lock:
            self.last_cycle_completed = now
            self.total_cycles += 1
            self.consecutive_clean_cycles += 1
            self.consecutive_detection_failures = 0

    def detection_failed(self, category: str, message: str) -> None:
        with self._lock:
            self.consecutive_detection_failures += 1
            self.total_detection_failures += 1
            self.consecutive_clean_cycles = 0
            self.last_error_category = category
            self.last_error_message = message[:240]

    def ha_ok(self, now: float, event_delivery: bool = False) -> None:
        with self._lock:
            self.last_ha_success = now
            self.consecutive_ha_failures = 0
            if event_delivery:
                self.last_event_delivery = now

    def ha_failed(self, message: str) -> None:
        with self._lock:
            self.consecutive_ha_failures += 1
            self.total_ha_failures += 1
            self.last_error_category = "home_assistant_api"
            self.last_error_message = message[:240]

    def evaluate(self, now: float, degraded_threshold: int, unavailable_seconds: int,
                 failure_ratio_threshold: float = 0.1, recovery_clean_cycles: int = 3) -> str:
        with self._lock:
            no_audio_for = now - (self.last_audio_success or self.started_monotonic)
            no_cycle_for = now - (self.last_cycle_completed or self.started_monotonic)
            self.ha_state = "degraded" if self.consecutive_ha_failures >= degraded_threshold else "healthy"
            if no_audio_for >= unavailable_seconds or no_cycle_for >= unavailable_seconds:
                return "failed"
            failure_ratio = self._rolling_failure_ratio()
            if (self.consecutive_audio_failures >= degraded_threshold or
                    self.consecutive_detection_failures >= degraded_threshold or
                    failure_ratio >= failure_ratio_threshold or
                    any(self._callback_overflow_results)):
                return "degraded"
            if self.state in {"degraded", "failed"} and self.consecutive_clean_cycles < recovery_clean_cycles:
                return "degraded"
            return "healthy"

    def _rolling_failure_ratio(self) -> float:
        if not self._capture_results:
            return 0.0
        return self._capture_results.count(False) / len(self._capture_results)

    def compact(self, now: float) -> dict[str, object]:
        with self._lock:
            return {
                "health": self.state, "ha_health": self.ha_state,
                "uptime_seconds": round(now - self.started_monotonic),
                "cycles": self.total_cycles, "audio_failures": self.total_audio_failures,
                "capture_attempts": self.capture_attempts,
                "successful_captures": self.successful_captures,
                "completed_detection_cycles": self.total_cycles,
                "rolling_audio_failure_ratio": round(self._rolling_failure_ratio(), 3),
                "rolling_capture_window": len(self._capture_results),
                "consecutive_clean_cycles": self.consecutive_clean_cycles,
                "consecutive_audio_failures": self.consecutive_audio_failures,
                "stream_restarts": self.stream_restarts, "ha_failures": self.total_ha_failures,
                "detection_failures": self.total_detection_failures,
                "last_error_category": self.last_error_category,
            }


def resource_metrics(health: RuntimeHealth, now: float) -> dict[str, object]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = int(usage.ru_maxrss * (1024 if os.uname().sysname == "Linux" else 1))
    virtual = None
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            virtual_pages, resident_pages, *_ = (int(value) for value in statm.read().split())
        page_size = os.sysconf("SC_PAGE_SIZE")
        virtual = virtual_pages * page_size
        rss = resident_pages * page_size
    except OSError:
        pass
    try:
        open_fds = len(os.listdir("/proc/self/fd"))
    except OSError:
        open_fds = -1
    try:
        load_1m = round(os.getloadavg()[0], 2)
    except OSError:
        load_1m = -1
    data = health.compact(now)
    data.update(rss_bytes=rss, virtual_bytes=virtual, open_fds=open_fds,
                threads=threading.active_count(), load_1m=load_1m,
                temp_files=len(glob.glob("/tmp/fire_audio_monitor_*.wav")))
    return data


class LoopWatchdog:
    def __init__(self, max_seconds: float, progress_deadline: Callable[[], float], on_stall: Callable[[], None]) -> None:
        self.max_seconds = max_seconds
        self.progress_deadline = progress_deadline
        self.on_stall = on_stall
        self._stop = threading.Event()
        self._triggered = threading.Event()
        self._thread = threading.Thread(target=self._run, name="detection-watchdog", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(min(5.0, max(0.1, self.max_seconds / 4))):
            if not self._triggered.is_set() and time.monotonic() > self.progress_deadline():
                self._triggered.set()
                self.on_stall()
                return

    def close(self) -> None:
        self._stop.set()
        if self._thread.ident is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
