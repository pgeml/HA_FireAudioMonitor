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
| `audio_capture_backend` | Capture backend. Use `sounddevice` for the preferred Home Assistant PulseAudio path or `arecord` for diagnostics/fallback. |
| `audio_input_device` | Audio input device selector. Use `pulse` for the preferred Home Assistant path, `default`, an input device index such as `"0"`, a case-insensitive name substring such as `"USB PnP"`, or an ALSA-style string such as `"hw:1,0"` or `"plughw:1,0"`. |
| `audio_diagnostics_only` | When true, run startup audio diagnostics once and exit without capturing audio or entering the detection loop. |
| `audio_diagnostics_on_startup` | When true, run full startup audio diagnostics once, then continue into the detection loop. |
| `min_rms` | Minimum RMS volume required before FFT matching can pass. |
| `min_band_energy_ratio` | Minimum share of FFT energy that must be inside the configured frequency band. |
| `frequency_min_hz` | Lower bound of the target alarm frequency band. |
| `frequency_max_hz` | Upper bound of the target alarm frequency band. |
| `required_hits` | Consecutive matching samples required before firing an event. |
| `clear_hits_required` | Consecutive clean samples required to clear and re-arm an active alarm. |
| `cooldown_seconds` | Minimum seconds between fired events. |
| `enable_presence_gate` | When true, read configured Home Assistant entities before firing. |
| `presence_entities` | Entity IDs checked through the Home Assistant API. |
| `trigger_when_presence_state` | Required entity state for the presence gate to pass. |
| `ha_event_type` | Home Assistant event type fired on detection. |
| `heartbeat_interval_seconds` | Health heartbeat interval; default `300` (minimum `60`). |
| `runtime_metrics_interval_seconds` | Resource log interval; default `600`. |
| `audio_failure_degraded_threshold` | Consecutive capture failures before degraded; default `3`. |
| `audio_failure_restart_threshold` | Consecutive failures between stream recreations; default `5`, and never below the degraded threshold. |
| `audio_unavailable_failure_seconds` | Time without a successful capture before failed/non-zero exit; default `600`. |
| `max_detection_cycle_seconds` | Independent stalled-loop watchdog deadline; default `180`. Validation reserves time for capture, diagnostics, and configured API calls. Normal sleep/backoff extends the next progress deadline and does not cause a false stall. |
| `audio_retry_backoff_seconds` | Delay after capture failure; default `5`. |
| `device_diagnostics_interval_seconds` | Minimum interval for full audio diagnostics; default `3600`. |

## Runtime health and lifecycle

The add-on owns one audio backend and one HTTP session for its lifetime. The `sounddevice` backend opens an explicit callback `InputStream`, reuses it, and closes it on capture failure, configured restart, signal, or shutdown. Capture data passes through a bounded queue with a deadline; overflow or PortAudio status errors invalidate the sample instead of analyzing discontinuous audio. The `arecord` backend starts one process per capture, discards subprocess stdout/stderr to prevent output growth, terminates then kills and reaps a timed-out child, and removes its uniquely named temporary WAV in every path. Run the separately rate-limited audio diagnostics for detailed ALSA command output.

Health is based on successful work, not process existence:

- `healthy`: capture and complete detection cycles are succeeding.
- `degraded`: consecutive audio or detection-cycle failures reached `audio_failure_degraded_threshold` while recovery continues. Home Assistant API availability is reported separately as `ha_health`.
- `failed`: no successful capture or complete detection cycle occurred for `audio_unavailable_failure_seconds`. Failure events are attempted and the process exits non-zero so Supervisor can restart it.
- recovery requires a complete successful capture and detection cycle.

The stable events are `fire_audio_monitor_heartbeat`, `fire_audio_monitor_health_changed`, `fire_audio_monitor_failed`, and `fire_audio_monitor_recovered`. Payloads contain compact counters, health, uptime, an error category, and timestamp—never credentials or audio. Heartbeats and metrics are rate-limited. Runtime logs include `rss_bytes`, `virtual_bytes`, `open_fds`, `threads`, `load_1m`, uptime, cycle/failure/restart counts, temporary-file count, and health.

SIGTERM and SIGINT only request shutdown; cleanup then runs in controlled application code to close/reap audio resources, close the HTTP pool, remove the active WAV, stop the watchdog, and log completion. If a call prevents loop progress longer than `max_detection_cycle_seconds`, the independent watchdog logs critical, gives backend cleanup two seconds, and then exits non-zero even if native cleanup is stuck. `boot: auto` starts the add-on with Home Assistant. Home Assistant's manifest `watchdog` field is a health-check URL, not a boolean, so this add-on does not declare it without an HTTP/TCP health endpoint. Supervisor's installed-add-on watchdog option is separate and should be enabled and verified on the target system if crash restart is required. A user-requested clean stop remains stopped. The add-on does not request additional privileges or bypass isolation.

### Failure and recovery notifications

```yaml
automation:
  - alias: Fire audio monitor failed
    triggers:
      - trigger: event
        event_type: fire_audio_monitor_failed
    actions:
      - action: notify.mobile_app_phone
        data:
          title: Fire audio monitoring unavailable
          message: "Health: {{ trigger.event.data.health }}; error: {{ trigger.event.data.last_error_category }}"
  - alias: Fire audio monitor recovered
    triggers:
      - trigger: event
        event_type: fire_audio_monitor_recovered
    actions:
      - action: notify.mobile_app_phone
        data:
          message: Fire audio monitoring recovered after a complete successful cycle.
```

### Missing heartbeat

Create an `input_datetime.fire_audio_monitor_last_heartbeat` with date and time enabled. Update it on heartbeat, then alert after 12 minutes (more than twice the default interval):

```yaml
automation:
  - alias: Store fire audio heartbeat
    triggers:
      - trigger: event
        event_type: fire_audio_monitor_heartbeat
    actions:
      - action: input_datetime.set_datetime
        target: {entity_id: input_datetime.fire_audio_monitor_last_heartbeat}
        data: {timestamp: "{{ now().timestamp() }}"}
  - alias: Fire audio heartbeat missing
    triggers:
      - trigger: time_pattern
        minutes: "/2"
    conditions:
      - condition: template
        value_template: >-
          {{ as_timestamp(now()) - state_attr('input_datetime.fire_audio_monitor_last_heartbeat', 'timestamp') > 720 }}
    actions:
      - action: notify.mobile_app_phone
        data: {message: "Fire Audio Monitor heartbeat missing for 12 minutes."}
```

Use the Supervisor integration add-on running sensor, when available, to notify on stopped/restarted state. A controlled restart automation should wait for the failed/missing condition for several minutes, notify first, call the supported Supervisor add-on restart service, and use a helper counter plus a cooldown (for example, at most two restarts per hour). Never restart on the alarm-detection event, never loop full-host reboots, and never suppress a genuine alarm notification.

## Home Assistant host safeguards

Keep host monitoring outside this add-on. Enable Home Assistant System Monitor entities for disk use, memory use, load, and processor temperature; alert at site-appropriate sustained thresholds. Monitor the Home Assistant URL from another machine/device so a dead Raspberry Pi can still be reported. Alert when the Supervisor add-on state is stopped or changes unexpectedly. Use an existing `notify.*` service. For disk/memory/temperature warnings, require persistence (for example 10 minutes) to avoid transient alerts. Use a UPS and an external availability monitor where safety requirements justify them.

## 10–14 day troubleshooting and validation

Run the normal `sounddevice` backend for at least 10 days, then optionally A/B against `arecord` using the same detector settings. Save add-on logs daily and record the `Runtime metrics` line. RSS and virtual memory may settle initially but should not trend upward without bound; `open_fds`, `threads`, and `temp_files` should return to a stable baseline (`temp_files=0` between arecord captures). Cycles should advance at the configured cadence, heartbeats should arrive, and stream restarts/audio failures should correlate with logged device errors.

If the host becomes unresponsive, collect before reboot when possible:

1. Add-on logs and Settings → System → Logs for Supervisor/Core/Host.
2. Add-on restart history and the last heartbeat timestamp.
3. Memory, swap, disk free space, load, CPU temperature, and the runtime RSS/FD/thread lines.
4. From an authorized Terminal/SSH add-on: `dmesg` or host journal entries for USB resets, ALSA, PortAudio, out-of-memory kills, I/O errors, and undervoltage.
5. Container process FD information (`/proc/<pid>/fd`) only where supported; do not weaken protection mode to obtain it.

Compare whether growth stops when the add-on is disabled and whether it differs between backends. Also test microphone unplug/replug, Core restart, add-on stop, and network/API interruption. This service supplements but does not replace certified, maintained fire detectors.

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

Start with conservative settings and test against a real alarm sound at normal distance. Raise `min_rms` if normal household noise causes hits. Raise `min_band_energy_ratio` if broad noise leaks into the band. Narrow the frequency band if unrelated tones match. Increase `required_hits` if short noises cause false positives. Increase `clear_hits_required` if the alarm clears too quickly between samples.

This add-on is a helper signal only. Keep certified smoke and fire detection hardware installed and maintained.

Recommended tuning workflow:

1. Disable the presence gate with `enable_presence_gate: false`.
2. Set `sample_interval_seconds` short, for example `5`.
3. Watch the RMS and dominant frequency logs in a quiet room.
4. Play or test the alarm sound at a realistic distance.
5. Adjust `min_rms`, `frequency_min_hz`, and `frequency_max_hz`.
6. Adjust `min_band_energy_ratio` if the frequency band is too permissive or too strict.
7. Increase `required_hits` if short sounds cause false positives.
8. Tune `clear_hits_required` so a continuous alarm does not rapidly clear and re-arm.
9. Enable the presence gate only after audio detection is behaving predictably.

The logs report configured thresholds, measured RMS, dominant frequency, band energy ratio, raw detection, confirmed detection, presence gate state, active alarm state, cooldown, and event status. Raw acoustic detection remains visible even when the presence gate blocks activation.

## Audio Troubleshooting

If the add-on logs `sounddevice.PortAudioError: Error querying device -1`, PortAudio cannot see a usable default input device inside the add-on container. The Home Assistant Audio dropdown is separate from `audio_input_device`, and the Home Assistant add-on UI audio input selection may not become the Python/PortAudio default input device.

The preferred Home Assistant add-on configuration is:

```yaml
audio_capture_backend: sounddevice
audio_input_device: pulse
```

Normal startup logs a concise audio summary. To log Linux audio device paths, `/proc/asound` files, `arecord` diagnostics, PortAudio host APIs, devices, the current `sounddevice.default.device`, selected audio environment variables, and filtered input devices, enable one of:

```yaml
audio_diagnostics_on_startup: true
```

```yaml
audio_diagnostics_only: true
```

`audio_diagnostics_only` exits after diagnostics. `audio_diagnostics_on_startup` continues into the detection loop after diagnostics.

If PortAudio shows no devices but `/dev/snd` exists, use ALSA direct capture:

```yaml
audio_capture_backend: arecord
audio_input_device: default
```

For a USB microphone exposed as `/dev/snd/pcmC1D0c`, the ALSA card/device is usually `plughw:1,0`. `plughw` is preferred over `hw` because it allows ALSA to perform format and rate conversion when needed.

If `arecord` reports `Cannot get card index`, `/dev/snd` device node visibility is probably not enough by itself. ALSA also needs card metadata from `/proc/asound`, especially files such as `/proc/asound/cards`, `/proc/asound/devices`, `/proc/asound/pcm`, and `/proc/asound/version`. Enable diagnostics-only mode to capture those logs without repeatedly attempting audio capture:

```yaml
audio_diagnostics_only: true
```

The Home Assistant Audio dropdown may not equal raw ALSA access inside an add-on container.

## Known Home Assistant Add-on Audio Limitation

`/dev/snd` visibility alone does not guarantee that raw ALSA card devices will work. Device paths such as `hw:0,0`, `hw:1,0`, and `plughw:1,0` require ALSA card metadata from `/proc/asound/cards`.

In the observed Home Assistant add-on environment, `/dev/snd` nodes are visible but `/proc/asound/cards`, `/proc/asound/devices`, and `/proc/asound/pcm` are missing. In that state, `arecord -l` reports no soundcards and raw card devices fail with `Cannot get card index`.

Try the Home Assistant-provided ALSA default path first:

```yaml
audio_capture_backend: arecord
audio_input_device: default
```

This produces an `arecord` command like:

```sh
arecord -D default -f S16_LE -r 16000 -c 1 -d 3 /tmp/fire_audio_monitor_sample.wav
```

When using Home Assistant's ALSA `default` device, the image needs the ALSA PulseAudio plugin. If the logs show `libasound_module_pcm_pulse.so` is missing, the Pulse ALSA plugin is not installed. This repository's Debian-based image installs `libasound2-plugins`; Alpine-based images would need the equivalent `alsa-plugins-pulse` package.

If `audio_input_device: default` also fails, raw microphone capture may require a different Home Assistant audio integration approach or a more privileged/custom container approach.

Examples:

```yaml
audio_capture_backend: sounddevice
audio_input_device: pulse
```

```yaml
audio_input_device: "USB PnP"
```

```yaml
audio_input_device: "0"
```

```yaml
audio_input_device: "hw:1,0"
```

```yaml
audio_input_device: "plughw:1,0"
```

Use `pulse` with `sounddevice` as the preferred Home Assistant path. Use `arecord` with `default` only as a diagnostic/fallback path when `/dev/snd` is visible but PortAudio cannot capture. Restart the add-on after changing the audio device configuration.

The add-on configuration uses Home Assistant's supported device path form:

```yaml
devices:
  - /dev/snd
```

Older Docker-style mappings such as `/dev/snd:/dev/snd:rwm` are not the current Home Assistant add-on config format; the supported equivalent is the host device path above.

The current Home Assistant add-on `map` option is for named Home Assistant folders such as `config`, `share`, and `media`; it is not a general arbitrary `/proc/asound` bind mount. If `/proc/asound` is missing inside the add-on, document the logs and test whether Home Assistant OS/Supervisor exposes another supported audio path before adding unsupported config fields.

For command-line diagnostics in an add-on shell or equivalent debug container, useful checks are:

```sh
ls -la /dev/snd
ls -la /proc/asound
cat /proc/asound/cards
cat /proc/asound/devices
cat /proc/asound/pcm
cat /proc/asound/version
arecord -l
arecord -L
arecord -D plughw:1,0 -f S16_LE -r 16000 -c 1 -d 3 /tmp/test.wav
```

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

## Local Testing

Create a local virtual environment and install the development dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements-dev.txt
```

Run the detector tests and a Python compile check:

```sh
python -m pytest fire_audio_monitor/tests
python -m compileall fire_audio_monitor/app fire_audio_monitor/tests
```

## Validation Ladder

Use this ladder to separate quick local checks from real Home Assistant hardware validation.

1. Local Python validation on Mac

   This verifies the pure Python detector tests and syntax/import compilation. It does not validate Docker packaging, Home Assistant add-on installation, Supervisor API access, or microphone access on Raspberry Pi.

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -U pip
   python -m pip install -r requirements-dev.txt
   python -m pytest fire_audio_monitor/tests
   python -m compileall fire_audio_monitor/app fire_audio_monitor/tests
   ```

   If macOS sandbox or cache permissions interfere with `compileall`, redirect bytecode cache output:

   ```sh
   PYTHONPYCACHEPREFIX=/private/tmp/fire_audio_monitor_pycache python -m compileall fire_audio_monitor/app fire_audio_monitor/tests
   ```

2. Docker image build validation

   Build the add-on image locally to catch Dockerfile and dependency installation problems:

   ```sh
   docker build -t fire-audio-monitor-test ./fire_audio_monitor
   ```

3. Optional container smoke test

   A plain local container can verify that the image starts, but it may fail once the app expects Home Assistant add-on runtime pieces such as `/data/options.json`, `SUPERVISOR_TOKEN`, Supervisor API access, or audio devices.

   macOS Docker may not expose a USB microphone the same way as Raspberry Pi / Home Assistant OS. Treat local Docker audio checks as limited packaging smoke tests, not final microphone validation.

4. Home Assistant Add-on Store install test

   Use the real add-on installation path:

   - Push the latest commit to GitHub.
   - Open Home Assistant.
   - Go to **Settings > Add-ons > Add-on Store**.
   - Open the three-dot menu and choose **Repositories**.
   - Add:

     ```text
     https://github.com/pgeml/HA_FireAudioMonitor
     ```

   - Reload the add-on store.
   - Install **Fire Audio Monitor**.
   - Configure options.
   - Start the add-on.
   - Inspect logs.

5. Raspberry Pi microphone/audio test

   Validate the real target setup on Raspberry Pi / Home Assistant OS with the USB microphone connected. Start with `log_level: debug`, confirm samples are being recorded, test with the actual alarm sound at realistic distance, and tune only after confirming the add-on can access audio reliably.
