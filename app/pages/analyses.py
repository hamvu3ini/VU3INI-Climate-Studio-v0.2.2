"""Analyses page: Area, Period, Analysis, Run -> results."""
from __future__ import annotations

import logging
import time

import solara
import solara.lab

import analyses
from analyses.base import Cancelled, RunContext
from app import state
from app.components.common import InfoCard, ParamsEditor, ResultView
from app.components.pickers import AreaPanel, PeriodPanel
from core import ee_client
from core import period as period_mod
from i18n import t
from storage import db

log = logging.getLogger(__name__)


@solara.component
def Page():
    by_mod = analyses.by_module()
    aid = state.analysis_id.value
    a = analyses.REGISTRY[aid]
    vals = {**a.defaults(), **(state.params.value or {})}

    @solara.lab.use_task(dependencies=None)
    def run():
        state.error.set("")
        state.cancel_requested.set(False)
        area = state.area.value
        t0 = time.time()
        try:
            ee_client.ensure()
            per = period_mod.check(state.period.value, a.datasets, needs_separate_baseline=a.needs_separate_baseline)
            ctx = RunContext(progress=lambda f, m: (state.progress.set(f), state.progress_msg.set(m)),
                             cancel_flag=lambda: state.cancel_requested.value)
            res = a.run(area, per, a.clean_params(vals), ctx)
            d = res.save()
            db.add_run(a.id, area.name, per.to_dict(), res.params, str(d))
            state.result.set(res)
            state.progress_msg.set(t("run.done", seconds=time.time() - t0))
        except Cancelled:
            state.progress_msg.set("Cancelled.")
        except ee_client.EENotReady:
            state.error.set(t("run.need_connection"))
        except Exception as e:  # noqa: BLE001
            log.exception("Analysis failed")
            state.error.set(f"{a.default_title} failed: {e}")

    with solara.Columns([2, 3]):
        with solara.Column():
            AreaPanel()
            PeriodPanel()
        with solara.Column():
            with solara.Card("Analysis"):
                values = [(f"{m}: {x.default_title}", x.id) for m, xs in by_mod.items() for x in xs]
                labels = [v[0] for v in values]
                cur = next(lab for lab, i in values if i == aid)

                def choose(lab):
                    new = next(i for lb, i in values if lb == lab)
                    state.analysis_id.set(new)
                    state.params.set({})
                    state.result.set(None)

                solara.Select(label="Choose an analysis", values=labels, value=cur, on_value=choose)
                InfoCard(t(f"info.{a.id}.what"), t(f"info.{a.id}.example"),
                         analyses.base.TUTORIALS.get(a.tutorial_no or 0, ""))
                ParamsEditor(a.params, vals, state.params.set)
                ready = state.area.value is not None
                with solara.Row():
                    solara.Button(t("run.run"), on_click=run, color="primary", icon_name="mdi-play", disabled=run.pending or not ready)
                    if run.pending:
                        solara.Button(t("run.cancel"), on_click=lambda: state.cancel_requested.set(True), icon_name="mdi-stop")
                if not ready:
                    solara.Text("Choose an area first (left).", style={"font-size": "0.85em"})
                if run.pending:
                    solara.ProgressLinear(value=state.progress.value * 100)
                if state.progress_msg.value:
                    solara.Text(state.progress_msg.value, style={"font-size": "0.85em"})
                if state.error.value:
                    solara.Error(state.error.value)
            res = state.result.value
            if res is not None and res.analysis_id == aid:
                ResultView(res, area=state.area.value)
