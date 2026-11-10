"""Earth Engine connection: project ID checks, sign-in, connection test.

The token is stored by the Earth Engine library on this PC only
(~/.config/earthengine/credentials); the project ID lives in settings.
"""
from __future__ import annotations

import logging
import re
import socket
from dataclasses import dataclass
from enum import Enum

from . import settings as settings_mod

log = logging.getLogger(__name__)

REGISTER_URL = "https://code.earthengine.google.com/register"
# Google Cloud project ID rules: 6-30 chars, lowercase letters, digits, hyphens,
# must start with a letter and must not end with a hyphen.
_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


class Status(str, Enum):
    CONNECTED = "connected"
    NOT_CONFIGURED = "not_configured"
    TOKEN_EXPIRED = "token_expired"
    NOT_REGISTERED = "not_registered"
    NO_INTERNET = "no_internet"
    INVALID_ID = "invalid_id"
    ERROR = "error"


@dataclass
class ConnectionResult:
    status: Status
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == Status.CONNECTED


_state: dict[str, object] = {"initialized_project": None, "last": None}


def valid_project_id(pid: str) -> bool:
    return bool(_PROJECT_RE.match((pid or "").strip()))


def _internet() -> bool:
    try:
        socket.create_connection(("earthengine.googleapis.com", 443), timeout=5).close()
        return True
    except OSError:
        return False


def classify_error(e: Exception) -> ConnectionResult:
    msg = str(e)
    low = msg.lower()
    if any(k in low for k in ("not registered", "not signed up", "register", "earthengine.googleapis.com/register")):
        return ConnectionResult(Status.NOT_REGISTERED, msg)
    if any(k in low for k in ("has not been used in project", "api has not been enabled", "service_disabled", "disabled")):
        return ConnectionResult(Status.NOT_REGISTERED, msg)
    if any(k in low for k in ("credentials", "invalid_grant", "token", "authenticate", "unauthenticated", "reauth")):
        return ConnectionResult(Status.TOKEN_EXPIRED, msg)
    if any(k in low for k in ("connection", "timed out", "name resolution", "unreachable", "max retries")):
        return ConnectionResult(Status.NO_INTERNET, msg)
    return ConnectionResult(Status.ERROR, msg)


def authenticate(force: bool = False) -> ConnectionResult:
    """One-time browser sign-in. Opens the user's browser."""
    import ee

    try:
        ee.Authenticate(auth_mode="localhost", force=force)
        return ConnectionResult(Status.CONNECTED, "Signed in")
    except Exception as e:  # noqa: BLE001
        log.exception("Earth Engine sign-in failed")
        return classify_error(e)


def initialize(project: str | None = None) -> ConnectionResult:
    import ee

    project = (project or settings_mod.load().ee_project or "").strip()
    if not project:
        return ConnectionResult(Status.NOT_CONFIGURED)
    if not valid_project_id(project):
        return ConnectionResult(Status.INVALID_ID, project)
    if _state["initialized_project"] == project:
        return ConnectionResult(Status.CONNECTED, project)
    try:
        ee.Initialize(project=project)
        _state["initialized_project"] = project
        return ConnectionResult(Status.CONNECTED, project)
    except Exception as e:  # noqa: BLE001
        if not _internet():
            return ConnectionResult(Status.NO_INTERNET, str(e))
        return classify_error(e)


def test_connection(project: str | None = None) -> ConnectionResult:
    """Initialise and run a tiny request to prove everything works."""
    import ee

    project = (project or settings_mod.load().ee_project or "").strip()
    _state["initialized_project"] = None  # force a fresh init
    res = initialize(project)
    if not res.ok:
        _state["last"] = res
        return res
    try:
        val = ee.Number(1).add(1).getInfo()
        assert val == 2
        # Touch a real public dataset so registration problems surface now.
        ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").limit(1).size().getInfo()
        res = ConnectionResult(Status.CONNECTED, project)
    except Exception as e:  # noqa: BLE001
        res = ConnectionResult(Status.NO_INTERNET, str(e)) if not _internet() else classify_error(e)
    _state["last"] = res
    return res


def sign_out() -> None:
    """Remove the stored token and the project ID."""
    try:
        import ee
        from pathlib import Path

        p = Path(ee.oauth.get_credentials_path())
        if p.exists():
            p.unlink()
    except Exception:
        log.exception("Could not remove Earth Engine credentials")
    s = settings_mod.load()
    s.ee_project = ""
    settings_mod.save(s)
    _state["initialized_project"] = None
    _state["last"] = None


def is_ready() -> bool:
    return _state["initialized_project"] is not None


def last_status() -> ConnectionResult | None:
    return _state["last"]  # type: ignore[return-value]


def ensure() -> None:
    """Raise a friendly error if Earth Engine is not usable."""
    if is_ready():
        return
    res = initialize()
    if not res.ok:
        raise EENotReady(res)


class EENotReady(RuntimeError):
    def __init__(self, res: ConnectionResult):
        super().__init__(f"Earth Engine not ready: {res.status.value} {res.detail}")
        self.result = res
