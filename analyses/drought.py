"""Drought module: VCI mapping (tutorial 1), temporal classification (2), MODIS monitoring (6)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core import ee_client
from core.area import Area
from core.period import Period
from core.result import ChartSpec, LegendItem, MapLayer

from . import eeh
from .base import Analysis, Param, RunContext, register
from .spi import spi_series

CLASS_LABELS = {i: c[1] for i, c in enumerate(eeh.DROUGHT_CLASSES)}
CLASS_COLOURS = [c[2] for c in eeh.DROUGHT_CLASSES]
CLASS_LEGEND = [LegendItem(c[1], c[2]) for c in eeh.DROUGHT_CLASSES]


def _bounds(p: dict[str, Any]) -> list[float]:
    return [p["t_extreme"], p["t_severe"], p["t_moderate"], p["t_mild"], 101]


THRESHOLD_PARAMS = [
    Param("t_extreme", "Extreme drought below", 10, "float", min=0, max=100),
    Param("t_severe", "Severe drought below", 20, "float", min=0, max=100),
    Param("t_moderate", "Moderate drought below", 30, "float", min=0, max=100),
    Param("t_mild", "Mild drought below", 40, "float", min=0, max=100),
]


def ndvi_collection(start, end, key: str = "modis_ndvi"):
    """MODIS NDVI scaled to -1..1, masked to good/marginal quality."""
    import ee

    from core.datasets import get

    col = ee.ImageCollection(get(key).ee_id).filterDate(start, end)

    def prep(img):
        q = img.select("SummaryQA")
        return (img.select("NDVI").multiply(0.0001).updateMask(q.lte(1))
                .rename("NDVI").copyProperties(img, ["system:time_start"]))

    return col.map(prep)


def lst_collection(start, end):
    """MODIS 8-day daytime LST in °C, masked to good/other-quality pixels."""
    import ee

    from core.datasets import get

    col = ee.ImageCollection(get("modis_lst8").ee_id).filterDate(start, end)

    def prep(img):
        qc = img.select("QC_Day").bitwiseAnd(3)
        return (img.select("LST_Day_1km").multiply(0.02).subtract(273.15).updateMask(qc.lte(1))
                .rename("LST").copyProperties(img, ["system:time_start"]))

    return col.map(prep)


def vci_collection(period: Period, key: str = "modis_ndvi"):
    """Monthly VCI images (0–100) for the analysis months."""
    import ee

    full_start = eeh.ee_date(period.start.replace(year=min(period.start.year, period.baseline_start), month=1, day=1))
    full_end = eeh.ee_date(period.end).advance(1, "month")
    ndvi_all = ndvi_collection(full_start, full_end, key)
    clim = eeh.monthly_climatology(ndvi_all, "NDVI", period.baseline_start, period.baseline_end)
    months = eeh.month_index_list(period.start, period.end, period.months)
    monthly = eeh.monthly_collection(ndvi_all, "NDVI", months)

    def to_vci(img):
        c = eeh.clim_for(clim, img.get("month"))
        mn, mx = c.select("NDVI_min"), c.select("NDVI_max")
        vci = img.subtract(mn).divide(mx.subtract(mn)).multiply(100).clamp(0, 100).rename("VCI")
        return vci.copyProperties(img, ["system:time_start", "month", "year", "label", "n"])

    return monthly.map(to_vci), clim, ndvi_all


def tci_collection(period: Period):
    import ee  # noqa: F401

    full_start = eeh.ee_date(period.start.replace(year=min(period.start.year, period.baseline_start), month=1, day=1))
    full_end = eeh.ee_date(period.end).advance(1, "month")
    lst_all = lst_collection(full_start, full_end)
    clim = eeh.monthly_climatology(lst_all, "LST", period.baseline_start, period.baseline_end)
    months = eeh.month_index_list(period.start, period.end, period.months)
    monthly = eeh.monthly_collection(lst_all, "LST", months)

    def to_tci(img):
        c = eeh.clim_for(clim, img.get("month"))
        mn, mx = c.select("LST_min"), c.select("LST_max")
        tci = mx.subtract(img).divide(mx.subtract(mn)).multiply(100).clamp(0, 100).rename("TCI")
        return tci.copyProperties(img, ["system:time_start", "month", "year", "label", "n"])

    return monthly.map(to_tci)


def _class_of(v: float, bounds: list[float]) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "No data"
    for b, (_, lab, _) in zip(bounds, eeh.DROUGHT_CLASSES):
        if v < b:
            return lab
    return "No drought"


def drought_events(df: pd.DataFrame, value_col: str, threshold: float, label_col: str = "label",
                   low_is_dry: bool = True) -> pd.DataFrame:
    """Contiguous runs of months at or beyond the threshold: onset, peak, end, length."""
    events, cur = [], None
    for _, r in df.iterrows():
        v = r[value_col]
        dry = (v < threshold) if low_is_dry else (v > threshold)
        if pd.notna(v) and dry:
            if cur is None:
                cur = {"Onset": r[label_col], "End": r[label_col], "Months": 1, "Peak month": r[label_col], "Peak value": v}
            else:
                cur["End"] = r[label_col]
                cur["Months"] += 1
                if (v < cur["Peak value"]) if low_is_dry else (v > cur["Peak value"]):
                    cur["Peak month"], cur["Peak value"] = r[label_col], v
        else:
            if cur:
                events.append(cur)
            cur = None
    if cur:
        events.append(cur)
    out = pd.DataFrame(events, columns=["Onset", "Peak month", "Peak value", "End", "Months"])
    if not out.empty:
        out["Peak value"] = out["Peak value"].round(2)
    return out


# ---------------------------------------------------------------------------
# Tutorial 1: drought mapping using VCI
# ---------------------------------------------------------------------------

@register
class DroughtVCI(Analysis):
    id = "drought_vci"
    tutorial_no = 1
    module = "Drought"
    default_title = "Drought mapping (VCI)"
    datasets = ["modis_ndvi"]
    needs_separate_baseline = False
    params = THRESHOLD_PARAMS + [
        Param("map_month", "Map shows", "last", "choice", ["last", "mean", "min"],
              help="last = most recent month; mean = average over the period; min = worst month per pixel"),
        Param("scale", "Output scale (m)", 250, "int", min=250, max=10000),
    ]

    def run(self, area: Area, period: Period, params: dict[str, Any] | None = None, ctx: RunContext | None = None):
        import ee

        ctx = ctx or RunContext()
        p = self.clean_params(params)
        ee_client.ensure()
        res = self.new_result(area, period, p)
        geom = area.to_ee()
        scale = max(p["scale"], area.suggested_scale(250))
        bounds = _bounds(p)

        ctx.step(0.1, "Building monthly VCI from MODIS NDVI")
        vci, _, _ = vci_collection(period)
        vci = vci.filter(ee.Filter.gt("n", 0))
        if p["map_month"] == "last":
            map_img = ee.Image(vci.sort("system:time_start", False).first())
            map_label = "latest month"
        elif p["map_month"] == "min":
            map_img = vci.min()
            map_label = "worst month per pixel"
        else:
            map_img = vci.mean()
            map_label = "average over the period"
        map_img = map_img.select("VCI").clip(geom)
        cls = eeh.classify(map_img, bounds)

        ctx.step(0.35, "Calculating area-average VCI for each month")
        series = eeh.region_series(vci, geom, ["VCI"], scale)
        series["VCI"] = series["VCI"].astype(float).round(1)
        series["Class"] = [_class_of(v, bounds) for v in series["VCI"]]

        ctx.step(0.7, "Measuring area in each drought class")
        areas = eeh.class_areas(cls, geom, scale, CLASS_LABELS)
        mean_v = eeh.region_value(map_img, geom, scale).get("VCI")

        ctx.step(0.9, "Preparing map, charts and summary")
        res.map_layers = [
            MapLayer("Drought class", cls, {"min": 0, "max": 4, "palette": CLASS_COLOURS}, CLASS_LEGEND,
                     legend_title=f"Drought class ({map_label})"),
            MapLayer("VCI (0–100)", map_img, {"min": 0, "max": 100, "palette": eeh.VCI_PALETTE}, [
                LegendItem("0 (worst)", eeh.VCI_PALETTE[0]), LegendItem("50", eeh.VCI_PALETTE[3]),
                LegendItem("100 (best)", eeh.VCI_PALETTE[-1])], legend_title="VCI", continuous=True, shown=False),
        ]
        res.stats = areas
        res.stats_title = f"Area by drought class ({map_label})"
        res.charts = [ChartSpec("Area-average VCI by month", "bar", series, "label", ["VCI"], y_title="VCI (0–100)",
                                extra={"hlines": [(b, lab) for b, (_, lab, _) in zip(bounds[:4], eeh.DROUGHT_CLASSES)],
                                       "color_by_class": True, "bounds": bounds[:4], "class_colours": CLASS_COLOURS})]
        dry = areas[areas["Class"] != "No drought"]["Share (%)"].sum()
        worst = series.loc[series["VCI"].idxmin()] if series["VCI"].notna().any() else None
        res.key_numbers = {"mean_vci": round(mean_v, 1) if mean_v is not None else None, "share_in_drought_pct": round(dry, 1),
                           "worst_month": None if worst is None else worst["label"],
                           "worst_month_vci": None if worst is None else worst["VCI"]}
        mv = f"{mean_v:.0f}" if mean_v is not None else "not available"
        res.summary = (
            f"For {area.name}, the vegetation condition index ({map_label}) averages {mv} on a 0–100 scale. "
            f"{dry:.0f}% of the area is in some level of drought (VCI below {p['t_mild']:g}). "
            + (f"The worst month for the area as a whole was {worst['label']} (VCI {worst['VCI']:.0f}, {worst['Class'].lower()})."
               if worst is not None else "")
        )
        res.method = {
            "formula": "VCI = 100 × (NDVI − NDVImin) / (NDVImax − NDVImin), min and max of monthly NDVI for the same calendar month across the baseline years",
            "datasets": ["MODIS/061/MOD13Q1 NDVI (250 m, 16-day), pixels with SummaryQA ≤ 1"],
            "baseline": period.baseline_label,
            "parameters": {"class thresholds": f"{bounds[:4]}", "map": map_label},
            "scale": f"{scale} m",
            "device": "Earth Engine (cloud)",
        }
        res.limitations = [
            "VCI reflects vegetation greenness; flooding, harvest or land-cover change can also lower it.",
            "Cloudy monsoon months have fewer clear MODIS pixels, so values then are less certain.",
        ]
        res.citations = ["Kogan, F. N. (1995). Application of vegetation index and brightness temperature for drought detection. Adv. Space Res. 15(11).",
                         "Didan, K. (2021). MOD13Q1 V061. NASA EOSDIS LP DAAC."]
        ctx.step(1.0, "Done")
        return res


# ---------------------------------------------------------------------------
# Tutorial 2: drought temporal classification
# ---------------------------------------------------------------------------

SPI_CLASSES = [(-2.0, "Extreme drought"), (-1.5, "Severe drought"), (-1.0, "Moderate drought"), (0.0, "Mild drought")]


def spi_class(v: float) -> str:
    if v is None or np.isnan(v):
        return "No data"
    for b, lab in SPI_CLASSES:
        if v <= b:
            return lab
    return "No drought"


@register
class DroughtTemporal(Analysis):
    id = "drought_temporal"
    tutorial_no = 2
    module = "Drought"
    default_title = "Drought temporal classification"
    datasets = ["modis_ndvi", "chirps"]
    params = THRESHOLD_PARAMS + [
        Param("index", "Index", "VCI", "choice", ["VCI", "SPI-1", "SPI-3", "SPI-6"],
              help="VCI = vegetation; SPI = rainfall-based Standardised Precipitation Index"),
        Param("scale", "Output scale (m)", 1000, "int", min=250, max=10000),
    ]

    def run(self, area: Area, period: Period, params: dict[str, Any] | None = None, ctx: RunContext | None = None):
        import ee

        ctx = ctx or RunContext()
        p = self.clean_params(params)
        ee_client.ensure()
        res = self.new_result(area, period, p)
        geom = area.to_ee()
        scale = max(p["scale"], area.suggested_scale(1000))
        bounds = _bounds(p)

        if p["index"] == "VCI":
            ctx.step(0.1, "Building monthly VCI")
            vci, _, _ = vci_collection(period, key="modis_ndvi_1km")
            vci = vci.filter(ee.Filter.gt("n", 0))
            ctx.step(0.3, "Area-average VCI per month")
            df = eeh.region_series(vci, geom, ["VCI"], scale)
            df["VCI"] = df["VCI"].astype(float).round(1)
            df["Class"] = [_class_of(v, bounds) for v in df["VCI"]]
            value_col, thr = "VCI", p["t_moderate"]
            events = drought_events(df, "VCI", thr)
            ctx.step(0.6, "Counting drought months per pixel")
            count = vci.map(lambda i: i.lt(thr).rename("dry")).sum().rename("dry_months").clip(geom)
            n_months = len(df)
            res.map_layers = [MapLayer("Months in moderate or worse drought", count,
                                       {"min": 0, "max": max(1, n_months // 2), "palette": ["#f7fcf5", "#fdae6b", "#e6550d", "#7f2704"]},
                                       [LegendItem("0", "#f7fcf5"), LegendItem(f"{max(1, n_months // 2)}+", "#7f2704")],
                                       legend_title="Drought months", continuous=True)]
            res.charts = [ChartSpec("Drought class by month (VCI)", "bar", df, "label", ["VCI"], y_title="VCI (0–100)",
                                    extra={"color_by_class": True, "bounds": bounds[:4], "class_colours": CLASS_COLOURS})]
            formula = "Monthly area-average VCI classified with the thresholds below; drought event = consecutive months below the moderate threshold"
            dsets = ["MODIS/061/MOD13A2 NDVI (1 km, 16-day)"]
        else:
            n = int(p["index"].split("-")[1])
            ctx.step(0.1, f"Monthly rainfall totals for SPI-{n}")
            df_all = chirps_monthly_area_totals(area, period, scale=max(scale, 5566), include_baseline=True)
            ctx.step(0.5, "Fitting gamma distributions per calendar month")
            spi = spi_series(df_all, "rain_mm", n, period.baseline_start, period.baseline_end)
            df = spi[(spi["date"] >= pd.Timestamp(period.start.replace(day=1))) & (spi["date"] <= pd.Timestamp(period.end))].copy()
            if period.months:
                df = df[df["date"].dt.month.isin(period.months)]
            df["label"] = df["date"].dt.strftime("%Y-%m")
            df["Class"] = [spi_class(v) for v in df["SPI"]]
            df = df[["label", "rain_mm", "SPI", "Class"]]
            value_col = "SPI"
            events = drought_events(df, "SPI", -1.0 + 1e-9)
            res.charts = [ChartSpec(f"SPI-{n} by month", "bar", df, "label", ["SPI"], y_title=f"SPI-{n}",
                                    extra={"signed": True, "hlines": [(-1, "Moderate"), (-1.5, "Severe"), (-2, "Extreme")]})]
            formula = (f"SPI-{n}: {n}-month rainfall totals fitted to a gamma distribution per calendar month over the baseline "
                       "(with zero-rain probability), transformed to a standard normal value. ≤ −2 extreme, −2 to −1.5 severe, "
                       "−1.5 to −1 moderate, −1 to 0 mild")
            dsets = ["UCSB-CHG/CHIRPS/DAILY rainfall, area totals per month"]

        ctx.step(0.9, "Summarising events")
        counts = df["Class"].value_counts().rename_axis("Class").reset_index(name="Months")
        res.stats = events if not events.empty else counts
        res.stats_title = "Drought events (onset, peak, end)" if not events.empty else "Months per class"
        res.charts.append(ChartSpec("Months per class", "bar", counts, "Class", ["Months"], y_title="Months"))
        longest = events.loc[events["Months"].idxmax()] if not events.empty else None
        res.key_numbers = {"months": len(df), "drought_events": len(events),
                           "longest_event_months": None if longest is None else int(longest["Months"]),
                           "longest_event": None if longest is None else f"{longest['Onset']} to {longest['End']}"}
        if longest is not None:
            res.summary = (f"Using {p['index']}, {area.name} had {len(events)} drought event(s) of moderate or worse level in "
                           f"{len(df)} months. The longest ran from {longest['Onset']} to {longest['End']} "
                           f"({int(longest['Months'])} months), peaking in {longest['Peak month']} ({value_col} {longest['Peak value']}).")
        else:
            res.summary = f"Using {p['index']}, {area.name} had no months of moderate or worse drought in the {len(df)} months analysed."
        res.method = {"formula": formula, "datasets": dsets, "baseline": period.baseline_label,
                      "parameters": {"index": p["index"], "thresholds": bounds[:4] if p["index"] == "VCI" else "standard SPI classes"},
                      "scale": f"{scale} m", "device": "Earth Engine (cloud); SPI fitting on this PC (CPU)"}
        res.limitations = ["Area averages hide differences inside the area; see the map for where drought was most frequent."
                           if p["index"] == "VCI" else
                           "SPI needs at least 30 baseline years for stable results; CHIRPS starts in 1981."]
        res.citations = ["McKee, T. B., Doesken, N. J., Kleist, J. (1993). The relationship of drought frequency and duration to time scales.",
                         "Kogan, F. N. (1995). Adv. Space Res. 15(11)."]
        ctx.step(1.0, "Done")
        return res


def chirps_monthly_area_totals(area: Area, period: Period, scale: int = 5566, include_baseline: bool = True) -> pd.DataFrame:
    """Area-average monthly rainfall totals (mm) from CHIRPS as a DataFrame with 'date' and 'rain_mm'."""
    import ee

    from core.datasets import get

    from datetime import date as _date

    start = _date(min(period.baseline_start, period.start.year) if include_baseline else period.start.year, 1, 1)
    months = eeh.month_index_list(start, period.end)
    col = ee.ImageCollection(get("chirps").ee_id).select("precipitation")
    monthly = eeh.monthly_collection(col, "precipitation", months, reducer="sum")
    df = eeh.region_series(monthly, area.to_ee(), ["precipitation"], scale)
    df["date"] = pd.to_datetime(df["label"] + "-01")
    df = df.rename(columns={"precipitation": "rain_mm"})[["date", "rain_mm"]]
    df["rain_mm"] = df["rain_mm"].astype(float)
    return df


# ---------------------------------------------------------------------------
# Tutorial 6: drought monitoring with MODIS (VCI, TCI, VHI)
# ---------------------------------------------------------------------------

@register
class DroughtMODIS(Analysis):
    id = "drought_modis"
    tutorial_no = 6
    module = "Drought"
    default_title = "Drought monitoring (MODIS VCI, TCI, VHI)"
    datasets = ["modis_ndvi_1km", "modis_lst8"]
    params = THRESHOLD_PARAMS + [
        Param("w_vci", "Weight of VCI in VHI", 0.5, "float", min=0, max=1),
        Param("map_month", "Map shows", "last", "choice", ["last", "mean", "min"]),
        Param("scale", "Output scale (m)", 1000, "int", min=1000, max=10000),
    ]

    def run(self, area: Area, period: Period, params: dict[str, Any] | None = None, ctx: RunContext | None = None):
        import ee

        ctx = ctx or RunContext()
        p = self.clean_params(params)
        ee_client.ensure()
        res = self.new_result(area, period, p)
        geom = area.to_ee()
        scale = max(p["scale"], area.suggested_scale(1000))
        bounds = _bounds(p)
        w = p["w_vci"]

        ctx.step(0.1, "Monthly VCI from MODIS NDVI")
        vci, _, _ = vci_collection(period, key="modis_ndvi_1km")
        ctx.step(0.2, "Monthly TCI from MODIS land temperature")
        tci = tci_collection(period)
        joined = ee.Join.inner().apply(vci, tci, ee.Filter.equals(leftField="label", rightField="label"))

        def combine(f):
            a, b = ee.Image(f.get("primary")), ee.Image(f.get("secondary"))
            vhi = a.multiply(w).add(b.multiply(1 - w)).rename("VHI")
            return a.addBands(b).addBands(vhi).copyProperties(a, ["system:time_start", "label", "month", "year"])

        both = ee.ImageCollection(joined.map(combine))
        ctx.step(0.4, "Area-average VCI, TCI and VHI per month")
        df = eeh.region_series(both, geom, ["VCI", "TCI", "VHI"], scale)
        for c in ("VCI", "TCI", "VHI"):
            df[c] = df[c].astype(float).round(1)
        df["VHI class"] = [_class_of(v, bounds) for v in df["VHI"]]

        if p["map_month"] == "last":
            m = ee.Image(both.sort("system:time_start", False).first())
            lab = "latest month"
        elif p["map_month"] == "min":
            m = both.min()
            lab = "worst month per pixel"
        else:
            m = both.mean()
            lab = "average over the period"
        m = m.clip(geom)
        vhi_cls = eeh.classify(m.select("VHI"), bounds)
        ctx.step(0.75, "Area in each VHI class")
        areas = eeh.class_areas(vhi_cls, geom, scale, CLASS_LABELS)
        idx_pal = {"min": 0, "max": 100, "palette": eeh.VCI_PALETTE}
        cont_leg = [LegendItem("0 (stressed)", eeh.VCI_PALETTE[0]), LegendItem("100 (healthy)", eeh.VCI_PALETTE[-1])]
        res.map_layers = [
            MapLayer("VHI class", vhi_cls, {"min": 0, "max": 4, "palette": CLASS_COLOURS}, CLASS_LEGEND, legend_title=f"VHI class ({lab})"),
            MapLayer("VHI", m.select("VHI"), idx_pal, cont_leg, "VHI", True, shown=False),
            MapLayer("VCI", m.select("VCI"), idx_pal, cont_leg, "VCI", True, shown=False),
            MapLayer("TCI", m.select("TCI"), idx_pal, cont_leg, "TCI", True, shown=False),
        ]
        res.stats = areas
        res.stats_title = f"Area by VHI class ({lab})"
        res.charts = [ChartSpec("VCI, TCI and VHI by month (area average)", "line", df, "label", ["VCI", "TCI", "VHI"],
                                y_title="Index (0–100)", extra={"hlines": [(p["t_moderate"], "Moderate drought")]})]
        latest = df.dropna(subset=["VHI"]).iloc[-1] if df["VHI"].notna().any() else None
        dry = areas[areas["Class"] != "No drought"]["Share (%)"].sum()
        res.key_numbers = {"latest_month": None if latest is None else latest["label"],
                           "latest_vhi": None if latest is None else latest["VHI"],
                           "latest_vci": None if latest is None else latest["VCI"],
                           "latest_tci": None if latest is None else latest["TCI"], "share_in_drought_pct": round(dry, 1)}
        if latest is not None:
            driver = "heat stress (low TCI)" if latest["TCI"] < latest["VCI"] else "poor greenness (low VCI)"
            res.summary = (f"In {latest['label']}, vegetation health (VHI) for {area.name} averaged {latest['VHI']:.0f} "
                           f"({latest['VHI class'].lower()}). VCI was {latest['VCI']:.0f} and TCI {latest['TCI']:.0f}, so the main "
                           f"stress came from {driver}. On the map ({lab}), {dry:.0f}% of the area is in some level of drought.")
        res.method = {"formula": f"TCI = 100 × (LSTmax − LST) / (LSTmax − LSTmin); VHI = {w:g} × VCI + {1 - w:g} × TCI",
                      "datasets": ["MODIS/061/MOD13A2 NDVI (1 km)", "MODIS/061/MOD11A2 daytime LST (1 km, 8-day), QC ≤ 1"],
                      "baseline": period.baseline_label, "parameters": {"VCI weight": w, "map": lab}, "scale": f"{scale} m",
                      "device": "Earth Engine (cloud)"}
        res.limitations = ["Cloud cover removes many MODIS pixels in monsoon months; the TCI is then based on fewer clear days."]
        res.citations = ["Kogan, F. N. (1995). Adv. Space Res. 15(11).", "Wan, Z. et al. (2021). MOD11A2 V061."]
        ctx.step(1.0, "Done")
        return res
