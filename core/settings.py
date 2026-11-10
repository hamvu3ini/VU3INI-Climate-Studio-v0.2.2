"""User settings, stored as JSON in the data folder. Secrets go to the OS keyring."""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from . import paths

log = logging.getLogger(__name__)
_lock = threading.Lock()

KEYRING_SERVICE = "VU3INI Climate Studio"


@dataclass
class Branding:
    organisation: str = ""
    author: str = "Sasanka Boruah (VU3INI)"
    contact: str = "hamvu3ini@gmail.com"
    logo_path: str = ""  # empty = bundled VU3INI logo


@dataclass
class StationMeta:
    name: str = "Premises station"
    latitude: float | None = None
    longitude: float | None = None
    elevation_m: float | None = None
    timezone: str = "Asia/Kolkata"
    irrigation_threshold_cb: float = 60.0
    rain_day_threshold_mm: float = 1.0


@dataclass
class Settings:
    # Earth Engine
    ee_project: str = ""
    notice_accepted_on: str = ""  # ISO date; empty = not accepted
    # Performance
    device_mode: str = "auto"  # auto | cpu | gpu
    hardware_cache: dict[str, Any] = field(default_factory=dict)
    benchmark_cache: dict[str, Any] = field(default_factory=dict)
    # AI
    ai_provider: str = "none"  # none | ollama | gemini | claude
    ai_model: str = ""
    ollama_url: str = "http://localhost:11434"
    # Units and display
    temp_unit: str = "C"
    rain_unit: str = "mm"
    wind_unit: str = "km/h"
    default_scale_m: int = 1000
    language: str = "en"
    theme: str = "light"
    # Reports
    branding: Branding = field(default_factory=Branding)
    page_size: str = "A4"
    # Station
    station: StationMeta = field(default_factory=StationMeta)

    @property
    def notice_accepted(self) -> bool:
        return bool(self.notice_accepted_on)

    def accept_notice(self) -> None:
        self.notice_accepted_on = datetime.now().date().isoformat()


def _from_dict(d: dict[str, Any]) -> Settings:
    s = Settings()
    for k, v in d.items():
        if k == "branding" and isinstance(v, dict):
            s.branding = Branding(**{kk: vv for kk, vv in v.items() if kk in Branding.__dataclass_fields__})
        elif k == "station" and isinstance(v, dict):
            s.station = StationMeta(**{kk: vv for kk, vv in v.items() if kk in StationMeta.__dataclass_fields__})
        elif k in Settings.__dataclass_fields__:
            setattr(s, k, v)
    return s


def load() -> Settings:
    f = paths.settings_file()
    if not f.exists():
        return Settings()
    try:
        return _from_dict(json.loads(f.read_text(encoding="utf-8")))
    except Exception:  # corrupt file: keep a copy, start fresh
        log.exception("Settings file unreadable; starting with defaults")
        f.rename(f.with_suffix(".bad.json"))
        return Settings()


def save(s: Settings) -> None:
    with _lock:
        f = paths.settings_file()
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(s), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)


# ---- secrets (API keys, SMTP passwords) -------------------------------------

def set_secret(name: str, value: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, name, value)


def get_secret(name: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        log.warning("Keyring unavailable; secret %s not read", name)
        return None


def delete_secret(name: str) -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, name)
    except Exception:
        pass
