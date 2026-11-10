"""Tiny translation layer.

All interface text lives in JSON catalogues (i18n/<lang>.json); code calls
t("key", **values). Missing keys fall back to English, then to the key itself,
so a partly translated language never breaks the app. Phase 4 adds fr, es,
zh-Hans, hi, ta and bn catalogues with the same keys.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
LANGUAGES = {
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "zh-Hans": "中文 (简体)",
    "hi": "हिन्दी",
    "ta": "தமிழ்",
    "bn": "বাংলা",
}
_current = "en"


@lru_cache(maxsize=None)
def _catalogue(lang: str) -> dict[str, str]:
    f = HERE / f"{lang}.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in LANGUAGES else "en"


def current() -> str:
    return _current


def available() -> dict[str, str]:
    """Languages whose catalogue exists."""
    return {k: v for k, v in LANGUAGES.items() if (HERE / f"{k}.json").exists()}


def t(key: str, lang: str | None = None, **values) -> str:
    lang = lang or _current
    text = _catalogue(lang).get(key) or _catalogue("en").get(key) or key
    try:
        return text.format(**values) if values else text
    except (KeyError, IndexError):
        return text
