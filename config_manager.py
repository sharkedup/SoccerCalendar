import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "calendars": {
        "primary":   {"label": "U13G Phoenix",      "url": ""},
        "secondary": {"label": "SPF Phoenix - SYSL", "url": ""}
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


def _normalize_calendars(calendars: dict) -> dict:
    """Accept the pre-2.0 shape ({"primary": "Some Calendar Name"}).

    The checker reads .ics URLs now, so an old-style name survives as the
    display label and the missing URL is reported as a config error rather
    than a silent empty read.
    """
    out = {}
    for which in ("primary", "secondary"):
        entry = calendars.get(which, DEFAULT_CONFIG["calendars"][which])
        if isinstance(entry, str):
            entry = {"label": entry, "url": ""}
        out[which] = {
            "label": entry.get("label", which),
            "url": (entry.get("url") or "").strip(),
        }
    return out


def load_config() -> dict:
    if CONFIG_PATH.exists():
        saved = json.loads(CONFIG_PATH.read_text())
        # Merge with defaults so new keys are always present
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        for section, values in saved.items():
            if isinstance(values, dict):
                merged[section] = {**merged.get(section, {}), **values}
            else:
                merged[section] = values
        merged["calendars"] = _normalize_calendars(merged.get("calendars", {}))
        return merged
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(config: dict):
    config = dict(config)
    config["calendars"] = _normalize_calendars(config.get("calendars", {}))
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
