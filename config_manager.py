import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "calendars": {
        "primary": "U13G Phoenix",
        "secondary": "SPF Phoenix"
    },
    "email": {
        "address": "",
        "gmail_app_password": "",
        "always_send": True
    },
    "schedule": {
        "check_time": "07:00"
    },
    "web": {
        "port": 8080
    }
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        saved = json.loads(CONFIG_PATH.read_text())
        # Merge with defaults so new keys are always present
        merged = DEFAULT_CONFIG.copy()
        for section, values in saved.items():
            if isinstance(values, dict):
                merged[section] = {**merged.get(section, {}), **values}
            else:
                merged[section] = values
        return merged
    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
