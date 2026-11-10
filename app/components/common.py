"""Shared UI pieces: info card, parameters, result tabs, explain panel, report panel."""
from __future__ import annotations

import logging
import webbrowser
from pathlib import Path

import solara
import solara.lab

from app import state
from core.result import AnalysisResult
from i18n import t
from reports import builder
from reports.charts import figure

from .mapview import MapView

log = logging.getLogger(__name__)


@solara.component
def InfoCard(what: str, example: str, tutorial_url: str = ""):
    with solara.Card(style={"background": "var(--vcs-band, #f1f3f5)", "border-left": "4px solid #1b4f8a", "margin": "6px 0"}):
        solara.Markdown(f"**What it does:** {what}")
        if example:
            solara.Markdown(f"**Easy example:** {example}  \n<small>{t('examples.note')}</small>")
        if tutorial_url:
            solara.Markdown(f"[Watch the method tutorial]({tutorial_url})")


@solara.component
def ParamsEditor(params_spec, values: dict, on_change):
    def setter(key):
        def f(v):
            on_change({**values, key: v})
        return f

    with solara.Details("Parameters (defaults are fine for most uses)", expand=False):
        for p in params_spec:
            v = values.get(p.key, p.default)
            if p.kind == "choice":
                solara.Select(label=p.label, values=list(p.choices or []), value=v, on_value=setter(p.key))
            elif p.kind == "int":
                solara.InputInt(label=p.label, value=int(v), on_value=setter(p.key))
            elif p.kind == "bool":
                solara.Checkbox(label=p.label, value=bool(v), on_value=setter(p.key))
            else:
                solara.InputFloat(label=p.label, value=float(v), on_value=setter(p.key))
            if p.help:
                solara.Text(p.help, style={"font-size": "0.8em", "color": "#666", "margin-top": "-8px"})


@solara.component
def StatsTable(res: AnalysisResult):
    if res.stats is None or res.stats.empty:
        solara.Text("No table for this result.")
        return
    solara.Markdown(f"**{res.stats_title}**")
    solara.DataFrame(res.stats, items_per_page=25)


@solara.component
def MethodView(res: AnalysisResult):
    rows = builder.context(res, builder.Assets(), builder.ReportOptions())["method_rows"]
    md = "\n".join(f"- **{k}:** {v}" for k, v in rows)
    solara.Markdown(md or "No method note.")
    if res.limitations:
        solara.Markdown("**Limitations**\n" + "\n".join(f"- {x}" for x in res.limitations))
    if res.citations:
        solara.Markdown("**Data sources**\n" + "\n".join(f"- {x}" for x in res.citations))


@solara.component
def ExplainPanel(res: AnalysisResult, on_update=None):
    from ai import explain as ex

    text, set_text = solara.use_state(res.ai_explanation)
    err, set_err = solara.use_state("")

    @solara.lab.use_task(dependencies=None)
    def run():
        set_err("")
        out = ex.explain(res, language=state.settings.value.language)
        if out.ok:
            res.ai_explanation = out.text
            set_text(out.text)
            if on_update:
                on_update(res)
        elif out.error == "not_configured":
            set_err(t("ai.not_configured"))
        else:
            set_err(f"The AI provider returned an error: {out.error}")

    solara.Markdown(f"**Summary (always available):** {res.summary}")
    solara.Button(t("result.explain"), on_click=run, disabled=run.pending, color="primary", icon_name="mdi-robot-outline")
    if run.pending:
        solara.ProgressLinear(True)
    if err:
        solara.Warning(err)
    if text:
        with solara.Card(style={"border": "1px dashed #888"}):
            solara.Markdown(f"_{t('ai.label')}_\n\n{text}")
            solara.Text("Included in the report's 'AI explanation' section (you can switch it off there).", style={"font-size": "0.8em", "color": "#666"})


@solara.component
def ReportPanel(res: AnalysisResult):
    s = state.settings.value
    sections, set_sections = solara.use_state({k: True for k in builder.SECTIONS})
    page, set_page = solara.use_state(s.page_size)
    landscape, set_landscape = solara.use_state(False)
    files, set_files = solara.use_state({})
    msg, set_msg = solara.use_state("")

    def opts():
        return builder.ReportOptions(sections=sections, page_size=page, landscape=landscape, branding=s.branding,
                                     language=s.language)

    @solara.lab.use_task(dependencies=None)
    def make(kind: str):
        set_msg("Preparing maps and charts…")
        o = opts()
        assets = builder.prepare_assets(res, o)
        out = dict(files)
        if kind in ("preview", "print"):
            p = builder.to_html(res, assets, o, auto_print=(kind == "print"))
            webbrowser.open(p.as_uri())
            out["html"] = str(p)
        elif kind == "word":
            out["docx"] = str(builder.to_docx(res, assets, o))
        elif kind == "pdf":
            out["pdf"] = str(builder.to_pdf(res, assets, o))
        set_files(out)
        set_msg("Done. Files are also saved in the Reports folder (Settings shows where).")

    with solara.Row(style={"flex-wrap": "wrap", "gap": "12px"}):
        with solara.Column():
            solara.Markdown(f"**{t('report.sections')}**")
            for k, lab in builder.SECTIONS.items():
                solara.Checkbox(label=lab, value=sections.get(k, True), on_value=lambda v, k=k: set_sections({**sections, k: v}))
        with solara.Column():
            solara.ToggleButtonsSingle(value=page, values=["A4", "Letter"], on_value=set_page)
            solara.Checkbox(label="Landscape", value=landscape, on_value=set_landscape)
            with solara.Row(style={"flex-wrap": "wrap"}):
                solara.Button(t("report.preview"), on_click=lambda: make("preview"), icon_name="mdi-file-eye-outline", disabled=make.pending)
                solara.Button(t("report.word"), on_click=lambda: make("word"), icon_name="mdi-file-word-outline", disabled=make.pending)
                solara.Button(t("report.pdf"), on_click=lambda: make("pdf"), icon_name="mdi-file-pdf-box", disabled=make.pending)
                solara.Button(t("report.print"), on_click=lambda: make("print"), icon_name="mdi-printer-outline", disabled=make.pending)
            if make.pending:
                solara.ProgressLinear(True)
            if msg:
                solara.Text(msg, style={"font-size": "0.85em"})
            if make.error:
                solara.Error(f"Report failed: {make.exception}")
            for kind, label, mime in (("docx", "Download Word file", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                                      ("pdf", "Download PDF", "application/pdf")):
                if files.get(kind) and Path(files[kind]).exists():
                    p = Path(files[kind])
                    solara.FileDownload(lambda p=p: p.read_bytes(), filename=p.name, label=label, mime_type=mime)


@solara.component
def ResultView(res: AnalysisResult, show_map: bool = True, area=None):
    tab, set_tab = solara.use_state(0)
    for w in res.warnings:
        solara.Warning(w, dense=True)
    solara.Markdown(f"### {res.title}\n{res.area_name} · {res.period_label}")
    if res.summary:
        solara.Info(res.summary)
    with solara.lab.Tabs(value=tab, on_value=set_tab):
        if show_map and res.map_layers:
            with solara.lab.Tab(t("result.map"), icon_name="mdi-map"):
                MapView(area=area, layers=res.map_layers)
        with solara.lab.Tab(t("result.charts"), icon_name="mdi-chart-line"):
            dark = state.settings.value.theme == "dark"
            for c in res.charts:
                solara.FigurePlotly(figure(c, dark=dark))
            if not res.charts:
                solara.Text("No charts for this result.")
        with solara.lab.Tab(t("result.table"), icon_name="mdi-table"):
            StatsTable(res)
        with solara.lab.Tab(t("result.method"), icon_name="mdi-information-outline"):
            if res.info_what:
                InfoCard(res.info_what, res.info_example, res.tutorial_url)
            MethodView(res)
        with solara.lab.Tab(t("result.explain"), icon_name="mdi-robot-outline"):
            ExplainPanel(res)
        with solara.lab.Tab(t("result.report"), icon_name="mdi-file-document-outline"):
            ReportPanel(res)
