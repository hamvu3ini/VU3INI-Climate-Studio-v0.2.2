"""Connect Earth Engine page (how-to + project ID + sign-in + test)."""
from __future__ import annotations

import webbrowser

import solara
import solara.lab

from app import state
from core import ee_client
from core import settings as settings_mod
from core.notice import NOTICE_FULL
from i18n import t

STATUS_TEXT = {
    "connected": ("success", "status.connected"),
    "not_configured": ("info", "status.not_configured"),
    "token_expired": ("warning", "status.token_expired"),
    "not_registered": ("error", "status.not_registered"),
    "no_internet": ("warning", "status.no_internet"),
    "invalid_id": ("error", "status.invalid_id"),
    "error": ("error", "status.error"),
}


def show_status(status: str, detail: str, project: str) -> None:
    kind, key = STATUS_TEXT.get(status, ("info", "status.not_configured"))
    msg = t(key, project=project, detail=detail[:300])
    {"success": solara.Success, "info": solara.Info, "warning": solara.Warning, "error": solara.Error}[kind](msg)


@solara.component
def Page():
    s = state.settings.value
    pid, set_pid = solara.use_state(s.ee_project)
    valid = ee_client.valid_project_id(pid)

    @solara.lab.use_task(dependencies=None)
    def signin():
        res = ee_client.authenticate()
        state.ee_status.set(res.status.value)
        state.ee_detail.set(res.detail)

    @solara.lab.use_task(dependencies=None)
    def test():
        res = ee_client.test_connection(pid)
        state.ee_status.set(res.status.value)
        state.ee_detail.set(res.detail)
        if res.ok:
            s2 = state.settings.value
            s2.ee_project = pid
            state.save_settings(s2)

    def save():
        s2 = state.settings.value
        s2.ee_project = pid
        state.save_settings(s2)

    def signout():
        ee_client.sign_out()
        state.ee_status.set("not_configured")
        state.settings.set(settings_mod.load())
        set_pid("")

    solara.Markdown(f"## {t('connect.title')}\n{t('connect.intro')}")
    with solara.Columns([3, 2]):
        with solara.Card("How to get free noncommercial access"):
            solara.Button(t("connect.open_register"), on_click=lambda: webbrowser.open(ee_client.REGISTER_URL),
                          icon_name="mdi-open-in-new", color="primary", outlined=True)
            solara.Markdown("\n".join(f"{i}. {t(f'connect.step{i}')}" for i in range(1, 7)))
            solara.Info(t("connect.tips"), dense=True)
        with solara.Card("Your project"):
            solara.InputText(t("connect.project_id"), value=pid, on_value=set_pid, continuous_update=True)
            solara.Text(t("connect.project_id_hint"), style={"font-size": "0.8em", "color": "#666"})
            if pid and not valid:
                solara.Error(t("status.invalid_id"), dense=True)
            with solara.Row(style={"flex-wrap": "wrap"}):
                solara.Button(t("connect.save"), on_click=save, disabled=not valid)
                solara.Button(t("connect.signin"), on_click=signin, icon_name="mdi-google", disabled=signin.pending)
                solara.Button(t("connect.test"), on_click=test, icon_name="mdi-lan-connect", color="primary",
                              disabled=test.pending or not valid)
                solara.Button(t("connect.signout"), on_click=signout, icon_name="mdi-logout", text=True)
            if signin.pending:
                solara.Info("A browser window opened for Google sign-in. Finish it there, then come back and click Test connection.")
            if test.pending:
                solara.ProgressLinear(True)
            show_status(state.ee_status.value, state.ee_detail.value, pid or s.ee_project)
            with solara.Details("Personal-use notice", expand=False):
                solara.Markdown(NOTICE_FULL)
