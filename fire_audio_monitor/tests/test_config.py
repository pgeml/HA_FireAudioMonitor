import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_config


def test_load_config_audio_diagnostics_on_startup(tmp_path):
    options_path = tmp_path / "options.json"
    options_path.write_text(
        json.dumps(
            {
                "audio_capture_backend": "sounddevice",
                "audio_input_device": "pulse",
                "audio_diagnostics_on_startup": True,
                "min_band_energy_ratio": 0.42,
                "clear_hits_required": 3,
            }
        ),
        encoding="utf-8",
    )

    config = load_config(options_path)

    assert config.audio_capture_backend == "sounddevice"
    assert config.audio_input_device == "pulse"
    assert config.audio_diagnostics_on_startup is True
    assert config.min_band_energy_ratio == 0.42
    assert config.clear_hits_required == 3
