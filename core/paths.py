"""Where the app keeps its files.

User data lives in %APPDATA%\\VU3INI Climate Studio on Windows (or the platform
equivalent elsewhere), never inside the program folder.
"""
from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "VU3INI Climate Studio"
APP_VERSION = "0.2.2"

# The program folder (where this source tree lives).
PROGRAM_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROGRAM_DIR / "assets"
LOGO_PATH = ASSETS_DIR / "vu3ini_logo.png"


def data_dir() -> Path:
    """Root folder for settings, database, cache, models and logs.

    Can be overridden with the VCS_DATA_DIR environment variable (used by tests
    and by a fully portable install).
    """
    override = os.environ.get("VCS_DATA_DIR")
    base = Path(override) if override else Path(user_data_dir(APP_NAME, appauthor=False, roaming=True))
    base.mkdir(parents=True, exist_ok=True)
    return base


def sub(name: str) -> Path:
    p = data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_file() -> Path:
    return data_dir() / "settings.json"


def database_file() -> Path:
    return data_dir() / "climate_studio.sqlite3"


def cache_dir() -> Path:
    return sub("cache")


def results_dir() -> Path:
    return sub("results")


def reports_dir() -> Path:
    return sub("reports")


def models_dir() -> Path:
    return sub("models")


def logs_dir() -> Path:
    return sub("logs")
