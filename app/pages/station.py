"""My Weather Station page: import wizard, quality check, reports, station vs satellite."""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import solara
import solara.lab

from app import state
from app.components.common import InfoCard, ResultView
from core import ee_client, paths
from i18n import t
from station import derive, importer, qc, reports, validation
from storage import db

log = logging.getLogger(__name__)
ALL_TARGETS = list(importer.SPECIAL) + list(importer.FIELDS)
LABELS = {**importer.SPECIAL, **importer.FIELDS}


@solara.component
def ImportWizard(station: str, on_done):
    raw, set_raw = solara.use_state(None)
    mapping, set_mapping = solara.use_state(None)
    report, set_report = solara.use_state(None)
    recs, set_recs = solara.use_state(None)
    msg, set_msg = solara.use_state("")
    err, set_err = solara.use_state("")
    exclude, set_exclude = solara.use_state(True)

    def on_file(f):
        try:
            p = paths.sub("uploads") / f["name"]
            p.write_bytes(f["file_obj"].read() if f.get("file_obj") else f["data"])
            r = importer.read_file(p)
            saved = db.get_mapping(station, r.signature)
            m = importer.Mapping.from_dict(saved) if saved else importer.guess_mapping(r, state.settings.value.station.timezone)
            set_raw(r)
            set_mapping(m)
            set_report(None)
            set_recs(None)
            set_err("")
            set_msg("Using your saved column mapping for this file layout." if saved else "")
        except Exception as e:  # noqa: BLE001
            set_err(f"Could not read the file: {e}")

    with solara.Card(t("station.import")):
        solara.Markdown("Export records from **WeatherLink** (desktop: *File → Export Records*; WeatherLink.com: *Data → Download*), "
                        "or convert `.wlk` archive files to CSV with the free [wlkReader](https://github.com/stevepr/wlkReader) tool.")
        solara.FileDrop(label="Drop a WeatherLink export (.txt or .csv) here", on_file=on_file, lazy=False)
        if err:
            solara.Error(err)
        if raw is None or mapping is None:
            return
        solara.Text(f"{raw.kind.capitalize()} export, {len(raw.data):,} rows. {msg}")
        for n in mapping.notes:
            solara.Info(n, dense=True)
        with solara.Details(t("station.mapping"), expand=True):
            solara.Markdown("Check what each column means. Columns marked *(ignore)* are not imported.")
            for h in raw.headers:
                sample = ", ".join(raw.data[h].head(3).astype(str))

                def setcol(v, h=h):
                    key = next(k for k, lab in LABELS.items() if lab == v)
                    set_mapping(importer.Mapping(**{**mapping.to_dict(), "columns": {**mapping.columns, h: key}}))

                with solara.Row(style={"align-items": "center"}):
                    solara.Text(h, style={"min-width": "170px", "font-weight": "600"})
                    solara.Select(label="", values=[LABELS[k] for k in ALL_TARGETS], value=LABELS[mapping.columns.get(h, "skip")],
                                  on_value=setcol, dense=True, style={"min-width": "240px"})
                    solara.Text(sample, style={"font-size": "0.8em", "color": "#666"})
            with solara.Row(style={"flex-wrap": "wrap"}):
                for kind, choices in importer.UNIT_CHOICES.items():
                    solara.Select(label=f"{kind.capitalize()} unit", values=choices, value=mapping.units.get(kind, choices[0]),
                                  on_value=lambda v, kind=kind: set_mapping(importer.Mapping(**{**mapping.to_dict(), "units": {**mapping.units, kind: v}})))
                solara.Select(label="Date order", values=["DMY", "MDY", "YMD"], value=mapping.date_order,
                              on_value=lambda v: set_mapping(importer.Mapping(**{**mapping.to_dict(), "date_order": v})))

        def check():
            try:
                df, msgs = importer.convert(raw, mapping)
                set_recs(df)
                set_report(qc.run(df))
                set_msg(" ".join(msgs))
                set_err("")
            except Exception as e:  # noqa: BLE001
                set_err(f"Conversion failed: {e}")

        solara.Button("Check the data", on_click=check, color="primary", icon_name="mdi-check-decagram")
        if report is not None:
            solara.Markdown(f"**{t('station.qc')}**")
            solara.DataFrame(report.summary_rows())
            if not report.flags.empty:
                with solara.Details(f"Flagged values ({len(report.flags):,})", expand=False):
                    solara.DataFrame(report.flags.head(500))
            if not report.gaps.empty:
                with solara.Details(f"Gaps ({len(report.gaps)})", expand=False):
                    solara.DataFrame(report.gaps)
            solara.Checkbox(label="Leave flagged values out of the analysis (recommended)", value=exclude, on_value=set_exclude)

            def do_import():
                df = qc.apply_exclusions(recs, report.flags) if exclude else recs
                ins, skip = db.upsert_station_records(station, df)
                db.save_mapping(station, raw.signature, mapping.to_dict())
                on_done(f"Imported {ins:,} new records ({skip:,} already in the database were skipped).")

            solara.Button("Import", on_click=do_import, color="success", icon_name="mdi-database-import")


@solara.component
def Page():
    s = state.settings.value
    station = s.station.name
    note, set_note = solara.use_state("")
    refresh, set_refresh = solara.use_state(0)
    first, last, n = db.station_range(station)

    solara.Markdown(f"## {t('station.title')}")
    with solara.Columns([2, 3]):
        with solara.Column():
            ImportWizard(station, lambda m: (set_note(m), set_refresh(refresh + 1)))
            if note:
                solara.Success(note)
            with solara.Card("Station details"):
                solara.Text(f"{station}: {n:,} records" + (f" from {first[:10]} to {last[:10]}" if first else " (none yet)"))
                lat, lon, el = s.station.latitude, s.station.longitude, s.station.elevation_m
                if lat is None or lon is None:
                    solara.Warning("Add the station's latitude, longitude and elevation in Settings > Station. They are needed for "
                                   "evapotranspiration and the station vs satellite comparison.", dense=True)
                else:
                    solara.Text(f"Location {lat:.4f}, {lon:.4f}; elevation {el or 0:.0f} m")
        with solara.Column():
            if n == 0:
                solara.Info("Import WeatherLink data to see reports.")
            else:
                StationReports(station, first, last, n, refresh)


@solara.component
def StationReports(station: str, first: str, last: str, n: int, refresh: int):
    s = state.settings.value
    rid, set_rid = solara.use_state("station_summary")
    rng, set_rng = solara.use_state("custom")
    start, set_start = solara.use_state(date.fromisoformat(first[:10]))
    end, set_end = solara.use_state(date.fromisoformat(last[:10]))
    err, set_err = solara.use_state("")

    def load_daily():
        recs = db.load_station(station)
        return recs, derive.daily(recs, lat=s.station.latitude, elev_m=s.station.elevation_m, wet_mm=s.station.rain_day_threshold_mm)

    recs, daily = solara.use_memo(load_daily, dependencies=[refresh, n, s.station.latitude, s.station.elevation_m])

    @solara.lab.use_task(dependencies=None)
    def build():
        set_err("")
        try:
            meta = {"irrigation_threshold_cb": s.station.irrigation_threshold_cb, "wet_mm": s.station.rain_day_threshold_mm}
            if rid == "station_validation":
                if s.station.latitude is None:
                    raise ValueError("Set the station location in Settings > Station first.")
                ee_client.ensure()
                sat = validation.fetch_satellite(s.station.latitude, s.station.longitude, start, end)
                res = validation.compare(daily, sat, station, start, end)
            else:
                res = reports.build(rid, daily, recs, station, start, end, meta)
            res.save()
            state.station_result.set(res)
        except ee_client.EENotReady:
            set_err(t("run.need_connection"))
        except Exception as e:  # noqa: BLE001
            log.exception("Station report failed")
            set_err(str(e))

    avail = reports.available(daily) + ["station_validation"]
    titles = {k: reports.REPORTS[k][0] for k in avail if k in reports.REPORTS}
    titles["station_validation"] = "Station vs satellite"
    with solara.Card("Reports"):
        solara.Select(label="Report", values=list(titles.values()), value=titles.get(rid, list(titles.values())[0]),
                      on_value=lambda v: set_rid(next(k for k, x in titles.items() if x == v)))
        InfoCard(t(f"info.{rid}.what"), t(f"info.{rid}.example"))

        def set_range(k):
            set_rng(k)
            if k != "custom":
                a, b = reports.period_bounds(k)
                set_start(a)
                set_end(b)

        solara.ToggleButtonsSingle(value=rng, values=list(reports.RANGES), on_value=set_range, dense=True)
        with solara.Row():
            solara.lab.InputDate(start, on_value=set_start, label="From")
            solara.lab.InputDate(end, on_value=set_end, label="To")
        solara.Button("Make report", on_click=build, color="primary", icon_name="mdi-chart-box-outline", disabled=build.pending)
        if build.pending:
            solara.ProgressLinear(True)
        if err:
            solara.Error(err)
    res = state.station_result.value
    if res is not None:
        ResultView(res, show_map=False)
