"""Station vs satellite: compare the Davis station with ERA5-Land, CHIRPS and SMAP at the same spot.

The numbers (bias, RMSE, correlation, percent bias) also serve as ground truth
for checking downscaled maps in Phase 3.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from core import ee_client
from core.datasets import get
from core.result import AnalysisResult, ChartSpec
from i18n import t


def fetch_satellite(lat: float, lon: float, start: date, end: date) -> pd.DataFrame:
    """Daily ERA5-Land temperature, CHIRPS rain and SMAP surface soil moisture at a point."""
    import ee

    ee_client.ensure()
    pt = ee.Geometry.Point([lon, lat])
    s, e = ee.Date(start.isoformat()), ee.Date(end.isoformat()).advance(1, "day")

    def series(col, bands, scale, rename):
        def f(img):
            v = img.select(bands).reduceRegion(ee.Reducer.mean(), pt, scale)
            return ee.Feature(None, v).set("day", img.date().format("YYYY-MM-dd"))
        rows = [x["properties"] for x in ee.FeatureCollection(col.map(f)).getInfo()["features"]]
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["day"] + list(rename.values()))
        return df.rename(columns=rename)

    era = series(ee.ImageCollection(get("era5_land_daily").ee_id).filterDate(s, e),
                 ["temperature_2m", "temperature_2m_max", "temperature_2m_min"], 11132,
                 {"temperature_2m": "era5_tmean_k", "temperature_2m_max": "era5_tmax_k", "temperature_2m_min": "era5_tmin_k"})
    for c in ("era5_tmean_k", "era5_tmax_k", "era5_tmin_k"):
        if c in era:
            era[c.replace("_k", "_c")] = era.pop(c).astype(float) - 273.15
    chirps = series(ee.ImageCollection(get("chirps").ee_id).filterDate(s, e), ["precipitation"], 5566,
                    {"precipitation": "chirps_rain_mm"})
    # SMAP L4 is 3-hourly; average to days on the server.
    smap_col = ee.ImageCollection(get("smap").ee_id).filterDate(s, e).select("sm_surface")
    n_days = (end - start).days + 1
    days = ee.List.sequence(0, n_days - 1)
    smap_daily = ee.ImageCollection.fromImages(days.map(
        lambda i: smap_col.filterDate(s.advance(i, "day"), s.advance(ee.Number(i).add(1), "day")).mean()
        .set("system:time_start", s.advance(i, "day").millis())))
    smap = series(smap_daily, ["sm_surface"], 11000, {"sm_surface": "smap_sm_m3m3"})
    out = era.merge(chirps, on="day", how="outer").merge(smap, on="day", how="outer")
    out["date"] = pd.to_datetime(out["day"])
    return out.drop(columns=["day"]).sort_values("date")


def metrics(obs: pd.Series, est: pd.Series) -> dict[str, float | None]:
    d = pd.DataFrame({"o": obs, "e": est}).dropna()
    if len(d) < 3:
        return {"n": len(d), "bias": None, "rmse": None, "r": None, "pbias_pct": None}
    diff = d["e"] - d["o"]
    r = float(np.corrcoef(d["o"], d["e"])[0, 1]) if d["o"].std() > 0 and d["e"].std() > 0 else None
    pb = float(100 * diff.sum() / d["o"].sum()) if d["o"].sum() != 0 else None
    return {"n": len(d), "bias": float(diff.mean()), "rmse": float(np.sqrt((diff ** 2).mean())), "r": r, "pbias_pct": pb}


def compare(daily: pd.DataFrame, sat: pd.DataFrame, station: str, start: date, end: date) -> AnalysisResult:
    dd = daily[(daily["date"] >= pd.Timestamp(start)) & (daily["date"] <= pd.Timestamp(end))]
    m = dd.merge(sat, on="date", how="inner")
    m["day"] = m["date"].dt.strftime("%Y-%m-%d")
    res = AnalysisResult(analysis_id="station_validation", title=f"Station vs satellite: {station}", area_name=station,
                         area_source="Davis Vantage Pro2 station", period_label=f"{start:%d %b %Y} – {end:%d %b %Y}",
                         info_what=t("info.station_validation.what"), info_example=t("info.station_validation.example"),
                         device_used="Earth Engine (cloud) + this PC (CPU)")
    rows, charts = [], []
    pairs = [("Mean temperature (°C)", "tmean_c", "era5_tmean_c", "ERA5-Land"),
             ("Daily high (°C)", "tmax_c", "era5_tmax_c", "ERA5-Land"),
             ("Daily low (°C)", "tmin_c", "era5_tmin_c", "ERA5-Land"),
             ("Daily rain (mm)", "rain_mm", "chirps_rain_mm", "CHIRPS")]
    for label, o, e, src in pairs:
        if o in m and e in m:
            k = metrics(m[o], m[e])
            rows.append({"Variable": label, "Satellite": src, "Days": k["n"],
                         "Bias (satellite − station)": None if k["bias"] is None else round(k["bias"], 2),
                         "RMSE": None if k["rmse"] is None else round(k["rmse"], 2),
                         "Correlation": None if k["r"] is None else round(k["r"], 2),
                         "Percent bias (%)": None if k["pbias_pct"] is None else round(k["pbias_pct"], 0)})
    if "tmean_c" in m and "era5_tmean_c" in m:
        charts.append(ChartSpec("Daily mean temperature: station vs ERA5-Land", "line", m, "day", ["tmean_c", "era5_tmean_c"], y_title="°C"))
    if "rain_mm" in m and "chirps_rain_mm" in m:
        mm = m.groupby(m["date"].dt.to_period("M"))[["rain_mm", "chirps_rain_mm"]].sum().round(1).reset_index()
        mm["Month"] = mm["date"].astype(str)
        charts.append(ChartSpec("Monthly rain: station vs CHIRPS", "bar", mm, "Month", ["rain_mm", "chirps_rain_mm"], y_title="mm"))
    soil_cols = [c for c in ("soil_1_cb", "soil_2_cb") if c in m and m[c].notna().any()]
    if soil_cols and "smap_sm_m3m3" in m:
        k = metrics(m[soil_cols[0]], m["smap_sm_m3m3"])
        rows.append({"Variable": "Soil moisture (pattern only)", "Satellite": "SMAP L4", "Days": k["n"], "Bias (satellite − station)": None,
                     "RMSE": None, "Correlation": None if k["r"] is None else round(k["r"], 2), "Percent bias (%)": None})
        charts.append(ChartSpec("Soil: station tension (cb, higher = drier) vs SMAP moisture (m³/m³, higher = wetter)", "line", m, "day",
                                [soil_cols[0], "smap_sm_m3m3"], extra={"secondary_y": "smap_sm_m3m3"}))
    res.stats = pd.DataFrame(rows)
    res.stats_title = "How close the satellite data is to the station"
    res.charts = charts
    rain = next((r for r in rows if r["Variable"] == "Daily rain (mm)"), None)
    temp = next((r for r in rows if r["Variable"] == "Mean temperature (°C)"), None)
    parts = []
    if rain and rain["Percent bias (%)"] is not None:
        pb = rain["Percent bias (%)"]
        parts.append(f"CHIRPS {'over' if pb > 0 else 'under'}-estimated total rain at the station by {abs(pb):.0f}%"
                     + (f" (daily correlation {rain['Correlation']:.2f})" if rain["Correlation"] is not None else "") + ".")
    if temp and temp["Bias (satellite − station)"] is not None:
        b = temp["Bias (satellite − station)"]
        parts.append(f"ERA5-Land daily mean temperature was on average {abs(b):.1f} °C {'warmer' if b > 0 else 'cooler'} than the station.")
    res.summary = " ".join(parts) or "Not enough overlapping days to compare."
    res.key_numbers = {"rain_pbias_pct": rain and rain["Percent bias (%)"], "temp_bias_c": temp and temp["Bias (satellite − station)"]}
    res.method = {"formula": "Bias = mean(satellite − station); RMSE; Pearson correlation; percent bias = 100 × Σ(satellite − station) ÷ Σ station",
                  "datasets": ["ECMWF/ERA5_LAND/DAILY_AGGR", "UCSB-CHG/CHIRPS/DAILY", get("smap").ee_id + " (sm_surface)"],
                  "device": "Earth Engine (cloud) + this PC (CPU)"}
    res.limitations = ["A grid cell (5–11 km) is compared with one point; some difference is expected, especially for rain.",
                       "Soil sensors measure tension (centibars) while SMAP gives volume (m³/m³), so only the pattern is compared.",
                       "Satellite days may not line up exactly with local calendar days, so daily rain correlations are lower than monthly ones."]
    res.citations = [get("era5_land_daily").citation, get("chirps").citation, get("smap").citation]
    return res
