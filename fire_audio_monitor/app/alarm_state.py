from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlarmTransition:
    raw_detected: bool
    confirmed_detected: bool
    presence_gate_open: bool
    active_alarm: bool
    event_fired: bool
    should_fire_event: bool
    hits: int
    clear_hits: int
    required_hits: int
    required_clear_hits: int
    cooldown_remaining_seconds: float
    event_status: str


class AlarmState:
    def __init__(self, required_hits: int, clear_hits_required: int, cooldown_seconds: int) -> None:
        self.required_hits = required_hits
        self.clear_hits_required = clear_hits_required
        self.cooldown_seconds = cooldown_seconds
        self.hits = 0
        self.clear_hits = 0
        self.confirmed_detected = False
        self.active_alarm = False
        self.last_event_at = 0.0

    def update(self, detected: bool, presence_gate_open: bool, now: float) -> AlarmTransition:
        if detected:
            self.hits += 1
            self.clear_hits = 0
        else:
            self.hits = 0
            self.clear_hits += 1

        if self.hits >= self.required_hits:
            self.confirmed_detected = True
        if self.clear_hits >= self.clear_hits_required:
            self.confirmed_detected = False

        previous_active_alarm = self.active_alarm
        self.active_alarm = self.confirmed_detected and presence_gate_open
        cooldown_remaining = self._cooldown_remaining(now)
        should_fire_event = (
            self.active_alarm
            and not previous_active_alarm
            and cooldown_remaining <= 0
        )
        event_status = self._event_status(detected, should_fire_event, cooldown_remaining)

        return AlarmTransition(
            raw_detected=detected,
            confirmed_detected=self.confirmed_detected,
            presence_gate_open=presence_gate_open,
            active_alarm=self.active_alarm,
            event_fired=False,
            should_fire_event=should_fire_event,
            hits=self.hits,
            clear_hits=self.clear_hits,
            required_hits=self.required_hits,
            required_clear_hits=self.clear_hits_required,
            cooldown_remaining_seconds=cooldown_remaining,
            event_status=event_status,
        )

    def mark_event_fired(self, now: float, transition: AlarmTransition) -> AlarmTransition:
        self.last_event_at = now
        return AlarmTransition(
            raw_detected=transition.raw_detected,
            confirmed_detected=transition.confirmed_detected,
            presence_gate_open=transition.presence_gate_open,
            active_alarm=transition.active_alarm,
            event_fired=True,
            should_fire_event=False,
            hits=transition.hits,
            clear_hits=transition.clear_hits,
            required_hits=transition.required_hits,
            required_clear_hits=transition.required_clear_hits,
            cooldown_remaining_seconds=0.0,
            event_status="event-fired",
        )

    def _cooldown_remaining(self, now: float) -> float:
        if self.last_event_at <= 0:
            return 0.0
        return max(0.0, self.cooldown_seconds - (now - self.last_event_at))

    def _event_status(self, detected: bool, should_fire_event: bool, cooldown_remaining: float) -> str:
        if should_fire_event:
            return "event-ready"
        if self.active_alarm and cooldown_remaining > 0:
            return "cooldown"
        if self.active_alarm:
            return "active-already-fired"
        if self.confirmed_detected and not self.active_alarm:
            return "presence-gate-blocked"
        if detected:
            return "waiting-for-required-hits"
        if self.clear_hits >= self.clear_hits_required:
            return "cleared"
        return "detector-not-matched"
