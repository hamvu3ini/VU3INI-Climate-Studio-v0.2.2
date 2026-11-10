"""VU3INI Climate Studio - Solara app entry point.

Run from the program folder:  solara run app/main.py --host 127.0.0.1
(The launcher does this for you.)
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import solara  # noqa: E402

from app import state  # noqa: E402
from app.pages import analyses as analyses_page  # noqa: E402
from app.pages import connect as connect_page  # noqa: E402
from app.pages import other  # noqa: E402
from app.pages import station as station_page  # noqa: E402
from core import ee_client, logging_setup  # noqa: E402
from core.notice import ACCEPT_LABEL, NOTICE_FULL  # noqa: E402
from core.paths import LOGO_PATH  # noqa: E402
from i18n import set_language, t  # noqa: E402

logging_setup.setup()
set_language(state.settings.value.language)

_ee_checked = {"done": False}


def _check_ee_once():
    if _ee_checked["done"]:
        return
    _ee_checked["done"] = True

    def work():
        res = ee_client.initialize()
        state.ee_status.set(res.status.value)
        state.ee_detail.set(res.detail)

    threading.Thread(target=work, daemon=True).start()


@solara.component
def NoticeGate(children=[]):
    s = state.settings.value
    ticked, set_ticked = solara.use_state(False)
    if s.notice_accepted:
        with solara.Column(children=children):
            pass
        return
    with solara.Card(t("notice.title"), style={"max-width": "720px", "margin": "40px auto"}):
        if LOGO_PATH.exists():
            solara.Image(str(LOGO_PATH), width="110px")
        solara.Markdown(NOTICE_FULL)
        solara.Checkbox(label=ACCEPT_LABEL, value=ticked, on_value=set_ticked)

        def accept():
            s2 = state.settings.value
            s2.accept_notice()
            state.save_settings(s2)

        solara.Button(t("notice.continue"), on_click=accept, disabled=not ticked, color="primary")
        if not ticked:
            solara.Text(t("notice.must_accept"), style={"font-size": "0.85em", "color": "#666"})


@solara.component
def StatusBar():
    from compute import device

    st = state.ee_status.value
    label = {"connected": "Earth Engine: connected", "not_checked": "Earth Engine: checking…",
             "not_configured": "Earth Engine: not set up"}.get(st, f"Earth Engine: {st.replace('_', ' ')}")
    color = "#2b8a3e" if st == "connected" else "#e67700"
    m = device.manager()
    with solara.Row(style={"font-size": "0.8em", "gap": "18px", "padding": "2px 8px", "color": "#495057"}):
        solara.Text(f"● {label}", style={"color": color})
        solara.Text(m.status_text())


_LOGO = LOGO_PATH.read_bytes() if LOGO_PATH.exists() else None


@solara.component
def Header():
    """Logo and app name in the top-left of the app bar on every page."""
    with solara.AppBarTitle():
        with solara.Row(style={"align-items": "center", "gap": "10px", "background": "transparent"}):
            if _LOGO:
                solara.Image(_LOGO, width="40px")
            solara.Text("VU3INI Climate Studio", style={"font-weight": "600", "font-size": "1.2rem", "color": "white"})


@solara.component
def Footer():
    from core.paths import APP_VERSION

    solara.HTML(
        tag="div",
        unsafe_innerHTML=(
            f"VU3INI Climate Studio v{APP_VERSION} &nbsp;·&nbsp; © 2026 Sasanka Boruah (VU3INI) &nbsp;·&nbsp; "
            "hamvu3ini@gmail.com &nbsp;·&nbsp; Personal, noncommercial use only. Results are estimates, not official data."
        ),
        style={"margin-top": "32px", "padding": "10px 8px", "border-top": "1px solid #dee2e6", "font-size": "0.8em",
               "color": "#6c757d", "text-align": "center"},
    )


def wrap(page_component):
    @solara.component
    def Wrapped():
        _check_ee_once()
        Header()
        StatusBar()
        with NoticeGate():
            page_component()
        Footer()
    return Wrapped


@solara.component
def Layout(children=[]):
    s = state.settings.value
    solara.Title(t("app.title"))
    if s.theme == "dark":
        solara.Style(":root { --vcs-band: #2b3035; }")
    return solara.AppLayout(children=children, color="#1b4f8a")


routes = [
    solara.Route(path="/", component=wrap(other.Home), label=t("nav.home")),
    solara.Route(path="connect", component=wrap(connect_page.Page), label=t("nav.connect")),
    solara.Route(path="analyses", component=wrap(analyses_page.Page), label=t("nav.analyses")),
    solara.Route(path="station", component=wrap(station_page.Page), label=t("nav.station")),
    solara.Route(path="ai", component=wrap(other.AIMap), label="AI mapping"),
    solara.Route(path="settings", component=wrap(other.Settings), label=t("nav.settings")),
    solara.Route(path="help", component=wrap(other.Help), label=t("nav.help")),
    solara.Route(path="about", component=wrap(other.About), label=t("nav.about")),
]
