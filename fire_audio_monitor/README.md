# Fire Audio Monitor

Fire Audio Monitor is a small Python Home Assistant add-on that listens to microphone audio and emits a Home Assistant event when a deterministic RMS and FFT check matches a configured fire alarm frequency band.

The MVP integration path is a Home Assistant internal event fired through the Home Assistant API. It does not use MQTT yet, does not use machine learning, and does not scan Wi-Fi or nearby devices.

This add-on provides assistive detection only. It is not certified fire detection equipment and must not replace properly installed, tested, and maintained smoke or fire alarms.

If enabled, the optional presence gate reads normal Home Assistant entity states through the Home Assistant API before allowing an alarm event. MQTT output is deferred and may be added later as an optional extension.

## Quick Start

1. Add this repository as a Home Assistant add-on repository.
2. Install **Fire Audio Monitor**.
3. Attach or expose a microphone to the add-on host/container.
4. Tune the frequency band and RMS threshold for your alarm.
5. Listen for the configured event type in automations.

Default event type:

```yaml
fire_audio_monitor_detected
```

Example automation trigger:

```yaml
alias: Possible fire alarm detected
trigger:
  - platform: event
    event_type: fire_audio_monitor_detected
action:
  - service: notify.mobile_app_phone
    data:
      title: "Possible fire alarm detected"
      message: "The fire audio monitor detected an alarm-like sound."
```

See [DOCS.md](DOCS.md) for configuration details and tuning notes.
