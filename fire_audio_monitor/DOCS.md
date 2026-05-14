# Fire Audio Monitor Documentation

## How Detection Works

Each loop records a short audio sample, calculates RMS volume, then runs an FFT over the sample. A hit is counted when:

- the RMS is at or above `min_rms`
- the strongest frequency in `frequency_min_hz` to `frequency_max_hz` has enough spectral energy relative to the full spectrum

After `required_hits` consecutive hits, the add-on fires a Home Assistant event and starts a cooldown. Non-matching samples reset the hit counter.

## Options

| Option | Description |
| --- | --- |
| `log_level` | Python logging level. |
| `sample_interval_seconds` | Delay between detection attempts. |
| `record_seconds` | Audio duration for each sample. |
| `min_rms` | Minimum RMS volume required before FFT matching can pass. |
| `frequency_min_hz` | Lower bound of the target alarm frequency band. |
| `frequency_max_hz` | Upper bound of the target alarm frequency band. |
| `required_hits` | Consecutive matching samples required before firing an event. |
| `cooldown_seconds` | Minimum seconds between fired events. |
| `enable_presence_gate` | When true, read configured Home Assistant entities before firing. |
| `presence_entities` | Entity IDs checked through the Home Assistant API. |
| `trigger_when_presence_state` | Required entity state for the presence gate to pass. |
| `ha_event_type` | Home Assistant event type fired on detection. |

## Presence Gate

When `enable_presence_gate` is true, at least one entity in `presence_entities` must have a state exactly matching `trigger_when_presence_state`. Entity states are read from the Home Assistant API exposed to add-ons by the Supervisor.

## Output Integration

The MVP output path is Option A only: the add-on fires a Home Assistant internal event through the Home Assistant API.

Default event type:

```yaml
fire_audio_monitor_detected
```

Example automation:

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

MQTT is intentionally deferred for the MVP. It can be added later as an optional second output path without changing the detector.

## Tuning

Start with conservative settings and test against a real alarm sound at normal distance. Raise `min_rms` if normal household noise causes hits. Narrow the frequency band if unrelated tones match. Increase `required_hits` if short noises cause false positives.

This add-on is a helper signal only. Keep certified smoke and fire detection hardware installed and maintained.

## Development Workflow

1. Local repo on Mac

   Work from a normal local folder, for example:

   ```sh
   cd /Users/<you>/Desktop/Dev/HA/Addon_FireAlarm
   ```

2. VS Code + Codex

   Open the folder in VS Code, edit the add-on files, and use Codex for small implementation passes, tests, and documentation updates. Keep detector changes focused and testable.

3. Optional devcontainer

   A devcontainer can be added later if you want repeatable Linux tooling for dependency installs, tests, and Home Assistant add-on build checks. For the MVP, local editing plus the add-on Dockerfile is enough.

4. Local Git commits

   Suggested first commit flow:

   ```sh
   git status
   git add .
   git commit -m "Initial fire audio monitor add-on scaffold"
   ```

5. Push to GitHub

   Create an empty GitHub repository, then connect and push:

   ```sh
   git remote add origin https://github.com/<user>/<repo>.git
   git branch -M main
   git push -u origin main
   ```

6. Add GitHub repo to Home Assistant add-on store

   In Home Assistant, go to **Settings > Add-ons > Add-on Store**, open the menu, choose **Repositories**, and add:

   ```text
   https://github.com/<user>/<repo>
   ```

7. Install add-on on Raspberry Pi

   After Home Assistant refreshes the store, install **Fire Audio Monitor** from the added repository. Rebuild/update the add-on after pushing changes to GitHub.

8. Configure USB microphone and options

   Connect the USB microphone to the Raspberry Pi or host running Home Assistant OS. Confirm the add-on has audio access, then tune `record_seconds`, `min_rms`, `frequency_min_hz`, `frequency_max_hz`, `required_hits`, and `cooldown_seconds`.

9. Watch logs and test detection

   Start with `log_level: debug`, watch the add-on logs, and test with the real alarm sound from a normal listening distance. Tune thresholds until normal household audio does not trigger repeated hits.

10. Create HA automation triggered by `fire_audio_monitor_detected` event

    Use an event automation to send notifications or trigger other Home Assistant actions:

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
