"""Rotating log file in the data folder."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import paths

_done = False


def setup(level: int = logging.INFO) -> None:
    global _done
    if _done:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(paths.logs_dir() / "climate_studio.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(fh)
    if sys.stderr is not None:  # no console under pythonw
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    _done = True
