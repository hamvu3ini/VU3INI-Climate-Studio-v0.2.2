"""Home, AI mapping, Settings, Help and About pages."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import replace
from datetime import date, timedelta

import solara
import solara.lab

import analyses
from ai import explain as ex
from ai import prithvi
from app import state
from app.components.common import InfoCard, ResultView
from app.components.pickers import AreaPanel
from compute import device
from core import datasets, ee_client, paths, settings as settings_mod
from core.notice import NOTICE_FULL
from i18n import available as langs_available
from i18n import t

log = logging.getLogger(__name__)


@solara.component
def Home():
    s = state.settings.value
    solara.Markdown(f"# {t('app.title')}\n{t('app.tagline')}")
    with solara.Columns([1, 1, 1]):
        with solara.Card("1. Connect Earth Engine"):
            st = state.ee_status.value
            solara.Text("Connected" if st == "connected" else "Not connected yet")
            solara.Button("Open", href="/connect", color="primary", outlined=True)
        with solara.Card("2. Run an analysis"):
            solara.Text(f"{len(analyses.REGISTRY)} analyses ready: drought and rainfall. Pick an area and a period, then Run.")
            solara.Button("Open", href="/analyses", color="primary", outlined=True)
        with solara.Card("3. My Weather Station"):
            solara.Text("Import WeatherLink data from the Davis Vantage Pro2 and make reports.")
            solara.Button("Open", href="/station", color="primary", outlined=True)
    solara.Markdown(f"**{device.manager().status_text()}**")
    for n in device.manager().hw.notes:
        solara.Warning(n, dense=True)
    from storage import db

    runs = db.list_runs(10)
    if not runs.empty:
        solara.Markdown("### Recent runs")
        solara.DataFrame(runs[["created", "analysis", "area_name"]])
    solara.Text(f"Data folder: {paths.data_dir()}", style={"font-size": "0.8em", "color": "#666"})


@solara.component
def AIMap():
    model_key, set_key = solara.use_state("prithvi_flood")
    start, set_start = solara.use_state(date.today() - timedelta(days=20))
    end, set_end = solara.use_state(date.today())
    err, set_err = solara.use_state("")
    res, set_res = solara.use_state(None)
    prog, set_prog = solara.use_state((0.0, ""))
    ok, why = prithvi.available()

    @solara.lab.use_task(dependencies=None)
    def run():
        set_err("")
        try:
            r = prithvi.run(model_key, state.area.value, start, end, progress=lambda f, m: set_prog((f, m)))
            r.save()
            set_res(r)
        except ee_client.EENotReady:
            set_err(t("run.need_connection"))
        except Exception as e:  # noqa: BLE001
            log.exception("Prithvi run failed")
            set_err(str(e))

    solara.Markdown("## AI mapping (NASA–IBM Prithvi)")
    if not ok:
        solara.Warning(why)
    with solara.Columns([2, 3]):
        with solara.Column():
            AreaPanel()
            with solara.Card("Model and dates"):
                solara.Select(label="Model", values=list(prithvi.MODELS), value=model_key, on_value=set_key)
                InfoCard(t(f"info.{model_key}.what"), t(f"info.{model_key}.example"))
                solara.Text(prithvi.MODELS[model_key].notes, style={"font-size": "0.8em", "color": "#666"})
                with solara.Row():
                    solara.lab.InputDate(start, on_value=set_start, label="Images from")
                    solara.lab.InputDate(end, on_value=set_end, label="Images to")
                d = device.manager()
                dev = d.choose("prithvi", 10)
                solara.Text(f"Will run on: {dev.label()} ({dev.reason})", style={"font-size": "0.85em"})
                solara.Button("Run AI model", on_click=run, color="primary", icon_name="mdi-robot",
                              disabled=run.pending or not ok or state.area.value is None)
                if run.pending:
                    solara.ProgressLinear(value=prog[0] * 100)
                    solara.Text(prog[1], style={"font-size": "0.85em"})
                if err:
                    solara.Error(err)
        with solara.Column():
            if res is not None:
                ResultView(res, area=state.area.value)


@solara.component
def Settings():
    s = state.settings.value
    msg, set_msg = solara.use_state("")
    bench, set_bench = solara.use_state(s.benchmark_cache)
    gem, set_gem = solara.use_state("")
    cla, set_cla = solara.use_state("")
    checks, set_checks = solara.use_state(None)

    def upd(**kw):
        new = replace(s, **kw)
        state.save_settings(new)
        set_msg("Saved.")

    def upd_station(**kw):
        upd(station=replace(s.station, **kw))

    def upd_brand(**kw):
        upd(branding=replace(s.branding, **kw))

    @solara.lab.use_task(dependencies=None)
    def run_bench():
        r = device.manager().benchmark()
        s2 = state.settings.value
        s2.benchmark_cache = r
        state.save_settings(s2)
        set_bench(r)

    @solara.lab.use_task(dependencies=None)
    def check_datasets():
        ee_client.ensure()
        set_checks(datasets.verify_all())

    solara.Markdown("## Settings")
    if msg:
        solara.Success(msg, dense=True)
    with solara.Columns([1, 1]):
        with solara.Column():
            with solara.Card("Performance (CPU / GPU)"):
                m = device.manager()
                solara.Text(m.status_text())
                for n in m.hw.notes:
                    solara.Warning(n, dense=True)

                def set_mode(v):
                    upd(device_mode=v)
                    device.reset(v)

                solara.ToggleButtonsSingle(value=s.device_mode, values=["auto", "cpu", "gpu"], on_value=set_mode)
                solara.Text("Auto picks the faster device for each task and falls back to CPU if the GPU runs out of memory.",
                            style={"font-size": "0.8em", "color": "#666"})
                solara.Button("Run speed test (about 20 s)", on_click=run_bench, disabled=run_bench.pending)
                if run_bench.pending:
                    solara.ProgressLinear(True)
                if bench:
                    solara.Markdown("```\n" + "\n".join(f"{k}: {v}" for k, v in bench.items()) + "\n```")
            with solara.Card("AI mapping components"):
                ok, why = prithvi.available()
                solara.Text("Installed and ready." if ok else why)
                ai_script = paths.PROGRAM_DIR / "packaging" / "install_ai.py"

                def install_ai():
                    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform.startswith("win") else 0
                    subprocess.Popen([sys.executable, str(ai_script)], cwd=str(paths.PROGRAM_DIR), creationflags=flags)
                    set_msg("The AI installer opened in a new window. Restart the app when it finishes.")

                solara.Button("Install or repair AI components", on_click=install_ai, disabled=not ai_script.exists(),
                              icon_name="mdi-download")
            with solara.Card("AI explanations"):
                solara.Select(label="Provider", values=["none", "ollama", "gemini", "claude"], value=s.ai_provider,
                              on_value=lambda v: upd(ai_provider=v, ai_model=""))
                if s.ai_provider == "ollama":
                    models = ex.ollama_models(s.ollama_url)
                    solara.InputText("Ollama address", value=s.ollama_url, on_value=lambda v: upd(ollama_url=v))
                    if models:
                        solara.Select(label="Model", values=models, value=s.ai_model if s.ai_model in models else models[0],
                                      on_value=lambda v: upd(ai_model=v))
                    else:
                        solara.Warning(f"Ollama is not running or has no models. Install Ollama from ollama.com, then run: "
                                       f"ollama pull {ex.default_model('ollama')}", dense=True)
                elif s.ai_provider in ("gemini", "claude"):
                    solara.InputText("Model name", value=s.ai_model or ex.default_model(s.ai_provider), on_value=lambda v: upd(ai_model=v))
                    if s.ai_provider == "gemini":
                        solara.InputText("Gemini API key (stored in Windows Credential Manager)", value=gem, on_value=set_gem, password=True)
                        solara.Button("Save key", on_click=lambda: (settings_mod.set_secret("gemini_api_key", gem), set_msg("Key saved.")))
                    else:
                        solara.InputText("Claude API key (stored in Windows Credential Manager)", value=cla, on_value=set_cla, password=True)
                        solara.Button("Save key", on_click=lambda: (settings_mod.set_secret("anthropic_api_key", cla), set_msg("Key saved.")))
                    solara.Text("Only summary numbers and method text are sent, never your files or raw data.",
                                style={"font-size": "0.8em", "color": "#666"})
            with solara.Card("Earth Engine datasets"):
                solara.Text("Checks every dataset ID and band against the live catalog. Run once after connecting.")
                solara.Button("Check datasets", on_click=check_datasets, disabled=check_datasets.pending)
                if check_datasets.pending:
                    solara.ProgressLinear(True)
                if check_datasets.error:
                    solara.Error(str(check_datasets.exception))
                if checks:
                    import pandas as pd

                    solara.DataFrame(pd.DataFrame([{"Dataset": c.key, "OK": "yes" if c.ok else "NO", "Message": c.message} for c in checks]))
        with solara.Column():
            with solara.Card("Station"):
                st = s.station
                solara.InputText("Station name", value=st.name, on_value=lambda v: upd_station(name=v))
                with solara.Row():
                    solara.InputFloat("Latitude", value=st.latitude, on_value=lambda v: upd_station(latitude=v), optional=True)
                    solara.InputFloat("Longitude", value=st.longitude, on_value=lambda v: upd_station(longitude=v), optional=True)
                    solara.InputFloat("Elevation (m)", value=st.elevation_m, on_value=lambda v: upd_station(elevation_m=v), optional=True)
                solara.InputFloat("Irrigation threshold (centibars)", value=st.irrigation_threshold_cb,
                                  on_value=lambda v: upd_station(irrigation_threshold_cb=v))
                solara.InputFloat("Rainy-day threshold (mm)", value=st.rain_day_threshold_mm, on_value=lambda v: upd_station(rain_day_threshold_mm=v))
                solara.InputText("Time zone", value=st.timezone, on_value=lambda v: upd_station(timezone=v))
            with solara.Card("Reports and branding"):
                b = s.branding
                solara.InputText("Author", value=b.author, on_value=lambda v: upd_brand(author=v))
                solara.InputText("Organisation", value=b.organisation, on_value=lambda v: upd_brand(organisation=v))
                solara.InputText("Contact", value=b.contact, on_value=lambda v: upd_brand(contact=v))
                solara.InputText("Logo file (empty = VU3INI logo)", value=b.logo_path, on_value=lambda v: upd_brand(logo_path=v))
                solara.ToggleButtonsSingle(value=s.page_size, values=["A4", "Letter"], on_value=lambda v: upd(page_size=v))
            with solara.Card("Language and display"):
                langs = langs_available()
                solara.Select(label="Interface language", values=list(langs.values()), value=langs.get(s.language, "English"),
                              on_value=lambda v: upd(language=next(k for k, x in langs.items() if x == v)))
                solara.Text("English ships now; French, Spanish, Mandarin Chinese, Hindi, Tamil and Bengali arrive in Phase 4.",
                            style={"font-size": "0.8em", "color": "#666"})
                solara.ToggleButtonsSingle(value=s.theme, values=["light", "dark"], on_value=lambda v: upd(theme=v))
            with solara.Card("Folders"):
                solara.Text(f"Data: {paths.data_dir()}")
                solara.Button("Open data folder", on_click=lambda: _open_folder(paths.data_dir()), text=True)
                solara.Button("Open reports folder", on_click=lambda: _open_folder(paths.reports_dir()), text=True)

                def clear_cache():
                    import shutil

                    shutil.rmtree(paths.cache_dir(), ignore_errors=True)
                    paths.cache_dir()
                    set_msg("Cache cleared.")

                solara.Button("Clear cache", on_click=clear_cache, text=True, color="error")


def _open_folder(p) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except Exception:
        log.exception("Could not open folder")


@solara.component
def Help():
    solara.Markdown("## Help\nEvery function explains itself. Example numbers are illustrations, not real results.")
    with solara.Details("Connecting Earth Engine", expand=False):
        solara.Markdown("\n".join(f"{i}. {t(f'connect.step{i}')}" for i in range(1, 7)) + f"\n\n{t('connect.tips')}")
    for module, items in analyses.by_module().items():
        solara.Markdown(f"### {module}")
        for a in items:
            with solara.Details(a.default_title, expand=False):
                InfoCard(t(f"info.{a.id}.what"), t(f"info.{a.id}.example"), analyses.base.TUTORIALS.get(a.tutorial_no or 0, ""))
    from station.reports import REPORTS

    solara.Markdown("### My Weather Station")
    for rid, (title, _) in list(REPORTS.items()) + [("station_validation", ("Station vs satellite", []))]:
        with solara.Details(title, expand=False):
            InfoCard(t(f"info.{rid}.what"), t(f"info.{rid}.example"))
    solara.Markdown("""### Glossary
- **NDVI**: a greenness measure from satellites (−1 to 1); healthy vegetation is high.
- **VCI / TCI / VHI**: vegetation condition, temperature condition and vegetation health indices (0–100).
- **SPI**: Standardised Precipitation Index; rain compared with what is normal for that month (below −1 is dry).
- **SDII**: average rain per rainy day.
- **Baseline**: the long-term 'normal' period that results are compared with.
- **CHIRPS, ERA5-Land, MODIS, GFS, Sentinel**: the satellite and model datasets used (see each report's sources).
- **Centibars (cb)**: soil water tension; higher means drier soil.
""")


@solara.component
def About():
    from core.paths import APP_VERSION, LOGO_PATH

    with solara.Row(style={"align-items": "center"}):
        if LOGO_PATH.exists():
            solara.Image(str(LOGO_PATH), width="120px")
        solara.Markdown(f"# VU3INI Climate Studio\nVersion {APP_VERSION}")
    solara.Markdown("""Owner: **Sasanka Boruah (VU3INI)** · Contact: hamvu3ini@gmail.com

Built on Google Earth Engine, NASA–IBM Prithvi models (Hugging Face), and open-source software:
Solara, ipyleaflet, earthengine-api, pandas, NumPy, SciPy, scikit-learn, Plotly, Matplotlib, python-docx, ReportLab,
GeoPandas, Shapely, pyproj, PyTorch and TerraTorch (when installed). Each keeps its own licence.

Data: MODIS (NASA LP DAAC / NSIDC), CHIRPS (UCSB Climate Hazards Center), ERA5-Land (ECMWF / Copernicus),
GFS (NOAA), Sentinel-1/2 (ESA Copernicus), SRTM (NASA/USGS), JRC Global Surface Water, FAO GAUL boundaries.""")
    solara.Markdown(f"**Personal-use notice:** {NOTICE_FULL}")
