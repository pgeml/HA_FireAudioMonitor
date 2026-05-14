# HA Fire Audio Monitor

This repository is a Home Assistant add-on repository for **Fire Audio Monitor**. The add-on lives in [`fire_audio_monitor/`](fire_audio_monitor/) and runs a small Python service that listens to microphone audio for alarm-like tones using deterministic RMS and FFT detection.

Fire Audio Monitor is assistive detection only. It is not certified fire detection equipment and must not replace installed, tested, and maintained smoke or fire alarms.

## MVP Scope

- Home Assistant internal event output only
- Presence gate through Home Assistant entities
- USB microphone / audio access on the Home Assistant host
- Deterministic RMS + FFT detection
- No MQTT output in the MVP
- No machine learning
- No direct Wi-Fi scanning

MQTT may be added later as an optional extension, but it is intentionally not part of the current runtime path.

## Add to Home Assistant

In Home Assistant, go to **Settings > Add-ons > Add-on Store**, open the menu, choose **Repositories**, and add:

```text
https://github.com/pgeml/HA_FireAudioMonitor
```

After the store refreshes, install **Fire Audio Monitor**, configure the options, and watch the add-on logs while tuning the microphone and detection thresholds.

## Documentation

See [`fire_audio_monitor/DOCS.md`](fire_audio_monitor/DOCS.md) for configuration, tuning, development workflow, and automation examples.
