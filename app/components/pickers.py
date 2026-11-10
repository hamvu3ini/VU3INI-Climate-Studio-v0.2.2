"""Area and Period panels."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import solara
import solara.lab

from app import state
from core import area as area_mod
from core import ee_client, paths
from core.period import Period, preset
from i18n import t
from storage import db

from .mapview import MapView

log = logging.getLogger(__name__)
METHODS = {"list": "area.method.list", "search": "area.method.search", "draw": "area.method.draw",
           "upload": "area.method.upload", "coords": "area.method.coords"}


@solara.component
def AreaPanel():
    method, set_method = solara.use_state("coords")
    err, set_err = solara.use_state("")

    def use(a):
        state.area.set(a)
        set_err("")

    with solara.Card(t("area.title")):
        solara.ToggleButtonsSingle(value=method, values=list(METHODS), on_value=set_method, dense=True,
                                   style={"flex-wrap": "wrap"})
        solara.Text(t(METHODS[method]), style={"font-weight": "600"})
        try:
            if method == "list":
                ListPicker(use, set_err)
            elif method == "search":
                SearchPicker(use, set_err)
            elif method == "draw":
                DrawPicker(use)
            elif method == "upload":
                UploadPicker(use, set_err)
            else:
                CoordPicker(use, set_err)
        except Exception as e:  # noqa: BLE001
            set_err(str(e))
        Favourites(use)
        if err:
            solara.Error(err, dense=True)
        a = state.area.value
        if a is not None:
            solara.Success(a.summary(), dense=True)
            for w in a.warnings():
                solara.Warning(w, dense=True)
            if a.method in ("list", "search"):
                solara.Text(t("area.boundary_note"), style={"font-size": "0.8em", "color": "#666"})


@solara.component
def ListPicker(use, set_err):
    country, set_country = solara.use_state("India")
    stt, set_state = solara.use_state("Assam")
    dist, set_dist = solara.use_state("")
    source = "FAO GAUL 2015"
    ready = ee_client.is_ready()

    countries = solara.lab.use_task(lambda: area_mod.admin_names(0, source=source) if ready else [], dependencies=[ready])
    states = solara.lab.use_task(lambda: area_mod.admin_names(1, {"ADM0_NAME": country}, source) if ready and country else [],
                                 dependencies=[ready, country])
    dists = solara.lab.use_task(lambda: area_mod.admin_names(2, {"ADM0_NAME": country, "ADM1_NAME": stt}, source) if ready and stt else [],
                                dependencies=[ready, country, stt])
    @solara.lab.use_task(dependencies=None)
    def pick():
        try:
            use(area_mod.from_admin(country, stt or None, dist or None, source))
        except Exception as e:  # noqa: BLE001
            set_err(f"Could not load that boundary: {e}")

    if not ready:
        solara.Warning(t("run.need_connection"), dense=True)
        return
    solara.Text(f"{t('area.source')}: {source}", style={"font-size": "0.85em"})
    solara.Select(label=t("area.country"), values=countries.value or [country], value=country,
                  on_value=lambda v: (set_country(v), set_state(""), set_dist("")))
    solara.Select(label=t("area.state"), values=[""] + (states.value or []), value=stt, on_value=lambda v: (set_state(v), set_dist("")))
    solara.Select(label=t("area.district") + " (optional)", values=[""] + (dists.value or []), value=dist, on_value=set_dist)
    if countries.pending or states.pending or dists.pending:
        solara.ProgressLinear(True)

    solara.Button("Use this area", on_click=pick, color="primary", disabled=pick.pending)


@solara.component
def SearchPicker(use, set_err):
    q, set_q = solara.use_state("")
    results, set_results = solara.use_state([])
    choice, set_choice = solara.use_state(None)
    radius, set_radius = solara.use_state(0.0)

    @solara.lab.use_task(dependencies=None)
    def search():
        try:
            r = area_mod.geocode(q)
            set_results(r)
            set_choice(r[0]["name"] if r else None)
            if not r:
                set_err("No places found. Try a different spelling or add the state.")
        except Exception as e:  # noqa: BLE001
            set_err(f"Search failed (needs internet): {e}")

    with solara.Row():
        solara.InputText("Place name", value=q, on_value=set_q, continuous_update=False)
        solara.Button("Search", on_click=search, disabled=search.pending or not q)
    if results:
        solara.Select(label="Results", values=[r["name"] for r in results], value=choice, on_value=set_choice)
        solara.InputFloat("Circle radius in km (0 = use the place's own boundary if it has one)", value=radius, on_value=set_radius)

        def go():
            r = next(x for x in results if x["name"] == choice)
            use(area_mod.from_search_result(r, radius_km=radius or None))

        solara.Button("Use this area", on_click=go, color="primary")


@solara.component
def DrawPicker(use):
    name, set_name = solara.use_state("Drawn area")
    drawn = state.drawn_geojson.value
    solara.Text("Use the drawing tools on the map (rectangle, polygon or circle). The last shape drawn is used.",
                style={"font-size": "0.85em"})
    MapView(area=state.area.value, draw=True, on_draw=state.drawn_geojson.set, height="380px")
    solara.InputText("Name", value=name, on_value=set_name)
    solara.Button("Use the drawn shape", on_click=lambda: use(area_mod.from_drawn(drawn, name)), color="primary", disabled=drawn is None)


@solara.component
def UploadPicker(use, set_err):
    path, set_path = solara.use_state(None)
    labels, set_labels = solara.use_state([])
    chosen, set_chosen = solara.use_state([])

    def on_file(f):
        try:
            p = paths.sub("uploads") / f["name"]
            p.write_bytes(f["file_obj"].read() if f.get("file_obj") else f["data"])
            gdf = area_mod.read_boundary_file(p)
            labs = area_mod.feature_labels(gdf)
            set_path(str(p))
            set_labels(labs)
            set_chosen(labs[:1] if len(labs) > 1 else labs)
            set_err("")
        except Exception as e:  # noqa: BLE001
            set_err(f"Could not read the boundary file: {e}")

    solara.FileDrop(label="Drop a Shapefile (.zip), KML/KMZ, GeoJSON or GeoPackage here", on_file=on_file, lazy=False)
    if path:
        solara.Text(f"File: {Path(path).name} ({len(labels)} feature(s))")
        if len(labels) > 1:
            solara.SelectMultiple("Features to use (several are merged)", values=chosen, all_values=labels, on_value=set_chosen)

        def go():
            idx = [labels.index(c) for c in chosen] if chosen else None
            use(area_mod.from_upload(path, idx, name=chosen[0] if len(chosen) == 1 else None))

        solara.Button("Use this area", on_click=go, color="primary")


@solara.component
def CoordPicker(use, set_err):
    lat, set_lat = solara.use_state(27.47)
    lon, set_lon = solara.use_state(94.91)
    r, set_r = solara.use_state(25.0)
    with solara.Row():
        solara.InputFloat("Latitude", value=lat, on_value=set_lat)
        solara.InputFloat("Longitude", value=lon, on_value=set_lon)
        solara.InputFloat(t("area.radius_km"), value=r, on_value=set_r)

    def go():
        try:
            use(area_mod.from_point(lat, lon, r))
        except ValueError as e:
            set_err(str(e))

    solara.Button("Use this area", on_click=go, color="primary")


@solara.component
def Favourites(use):
    favs = db.list_areas()
    names = [n for n, _ in favs]
    sel, set_sel = solara.use_state("")
    new_name, set_new = solara.use_state("")
    with solara.Details("Saved areas", expand=False):
        if names:
            solara.Select(label="Open a saved area", values=[""] + names, value=sel, on_value=set_sel)
            if sel:
                from core.area import Area

                solara.Button("Use", on_click=lambda: use(Area.from_dict(dict(favs)[sel])))
        a = state.area.value
        if a is not None:
            solara.InputText("Save current area as", value=new_name or a.name, on_value=set_new)
            solara.Button("Save", on_click=lambda: db.save_area(new_name or a.name, a.to_dict()))


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@solara.component
def PeriodPanel(steps: tuple[str, ...] = ("monthly",)):
    p: Period = state.period.value
    pre, set_pre = solara.use_state("custom")

    def upd(**kw):
        d = p.to_dict()
        d.update(kw)
        state.period.set(Period.from_dict(d))

    def apply_preset(name):
        set_pre(name)
        if name != "custom":
            n = preset(name)
            n.baseline_start, n.baseline_end, n.step = p.baseline_start, p.baseline_end, p.step
            state.period.set(n)

    with solara.Card(t("period.title")):
        solara.ToggleButtonsSingle(value=pre, values=["last12", "last3y", "monsoon", "custom"], on_value=apply_preset, dense=True)
        with solara.Row():
            solara.lab.InputDate(p.start, on_value=lambda v: upd(start=v.isoformat()), label=t("period.from"))
            solara.lab.InputDate(p.end, on_value=lambda v: upd(end=v.isoformat()), label=t("period.to"))
        solara.SelectMultiple(t("period.season") + " (none = all months)", values=[MONTHS[m - 1] for m in p.months], all_values=MONTHS,
                              on_value=lambda v: upd(months=sorted(MONTHS.index(x) + 1 for x in v)))
        with solara.Row():
            solara.InputInt("Baseline from (year)", value=p.baseline_start, on_value=lambda v: upd(baseline_start=v))
            solara.InputInt("Baseline to (year)", value=p.baseline_end, on_value=lambda v: upd(baseline_end=v))
        if len(steps) > 1:
            solara.Select(label=t("period.step"), values=list(steps), value=p.step if p.step in steps else steps[0],
                          on_value=lambda v: upd(step=v))
        solara.Text(f"{p.label}; baseline {p.baseline_label}", style={"font-size": "0.85em", "color": "#555"})
        if p.end > date.today():
            solara.Warning("The end date is in the future; it will be set to today.", dense=True)
