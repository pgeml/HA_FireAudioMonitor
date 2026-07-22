import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.alarm_state import AlarmState


def test_one_hit_does_not_fire_when_two_required():
    state = AlarmState(required_hits=2, clear_hits_required=2, cooldown_seconds=60)

    transition = state.update(detected=True, presence_gate_open=True, now=1.0)

    assert transition.raw_detected is True
    assert transition.confirmed_detected is False
    assert transition.active_alarm is False
    assert transition.should_fire_event is False
    assert transition.event_status == "waiting-for-required-hits"


def test_two_consecutive_hits_fire_one_event():
    state = AlarmState(required_hits=2, clear_hits_required=2, cooldown_seconds=60)

    state.update(detected=True, presence_gate_open=True, now=1.0)
    transition = state.update(detected=True, presence_gate_open=True, now=2.0)

    assert transition.confirmed_detected is True
    assert transition.active_alarm is True
    assert transition.should_fire_event is True
    assert transition.event_status == "event-ready"


def test_continuous_hits_do_not_repeatedly_fire_events():
    state = AlarmState(required_hits=2, clear_hits_required=2, cooldown_seconds=60)

    state.update(detected=True, presence_gate_open=True, now=1.0)
    transition = state.update(detected=True, presence_gate_open=True, now=2.0)
    transition = state.mark_event_fired(2.0, transition)
    assert transition.event_fired is True

    next_transition = state.update(detected=True, presence_gate_open=True, now=3.0)

    assert next_transition.active_alarm is True
    assert next_transition.should_fire_event is False
    assert next_transition.event_status == "cooldown"


def test_clean_samples_clear_active_alarm_and_rearm_after_cooldown():
    state = AlarmState(required_hits=2, clear_hits_required=2, cooldown_seconds=5)

    state.update(detected=True, presence_gate_open=True, now=1.0)
    transition = state.update(detected=True, presence_gate_open=True, now=2.0)
    state.mark_event_fired(2.0, transition)

    first_clear = state.update(detected=False, presence_gate_open=True, now=3.0)
    second_clear = state.update(detected=False, presence_gate_open=True, now=4.0)

    assert first_clear.active_alarm is True
    assert second_clear.confirmed_detected is False
    assert second_clear.active_alarm is False
    assert second_clear.event_status == "cleared"

    state.update(detected=True, presence_gate_open=True, now=8.0)
    rearmed = state.update(detected=True, presence_gate_open=True, now=9.0)

    assert rearmed.active_alarm is True
    assert rearmed.should_fire_event is True


def test_presence_gate_closed_blocks_active_alarm_without_hiding_raw_detection():
    state = AlarmState(required_hits=2, clear_hits_required=2, cooldown_seconds=60)

    state.update(detected=True, presence_gate_open=False, now=1.0)
    transition = state.update(detected=True, presence_gate_open=False, now=2.0)

    assert transition.raw_detected is True
    assert transition.confirmed_detected is True
    assert transition.presence_gate_open is False
    assert transition.active_alarm is False
    assert transition.should_fire_event is False
    assert transition.event_status == "presence-gate-blocked"


def test_presence_gate_open_allows_confirmed_detection_to_become_active():
    state = AlarmState(required_hits=2, clear_hits_required=2, cooldown_seconds=60)

    state.update(detected=True, presence_gate_open=False, now=1.0)
    state.update(detected=True, presence_gate_open=False, now=2.0)
    transition = state.update(detected=True, presence_gate_open=True, now=3.0)

    assert transition.confirmed_detected is True
    assert transition.active_alarm is True
    assert transition.should_fire_event is True


def test_counters_saturate():
    state = AlarmState(required_hits=2, clear_hits_required=3, cooldown_seconds=0)
    for _ in range(100):
        transition = state.update(True, True, 1.0)
    assert transition.hits == 2
    for _ in range(100):
        transition = state.update(False, True, 2.0)
    assert transition.clear_hits == 3
