"""Station reports: each returns an AnalysisResult the UI and report engine already understand."""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

import numpy as np
import pandas as pd

from core.result import AnalysisResult, ChartSpec
from i18n import t

from . import derive

REPORTS: dict[str, tuple[str, list[str]]] = {
    # id: (title, daily columns needed - any one is enough)
    "station_temperature": ("Temperature", ["tmax_c"]),
    "station_rainfall": ("Rainfall", ["rain_mm"]),
    "station_wind": ("Wind", ["wind_mean_kmh", "gust_max_kmh"]),
    "station_humidity": ("Humidity and dew point", ["rh_mean"]),
    "station_feelslike": ("Feels-like (heat index and wind chill)", ["heat_index_max_c"]),
    "station_pressure": ("Pressure", ["bar_mean_hpa"]),
    "station_solar": ("Solar radiation", ["solar_mj"]),
    "station_uv": ("UV index", ["uv_max"]),
    "station_soil": ("Soil moisture", ["soil_1_cb", "soil_2_cb", "soil_3_cb", "soil_4_cb"]),
    "station_et": ("Evapotranspiration and water balance", ["et_mm"]),
    "station_summary": ("Monthly and yearly climate summary", ["tmax_c", "rain_mm"]),
}
RANGES = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly", "seasonal": "Seasonal (monsoon / winter)", "custom": "Custom"}


def available(daily: pd.DataFrame) -> list[str]:
    return [k for k, (_, need) in REPORTS.items() if any(c in daily and daily[c].notna().any() for c in need)]


def _fmt_day(d) -> str:
    return pd.Timestamp(d).strftime("%d %b %Y")


def _base(rid: str, station: str, start: date, end: date, daily: pd.DataFrame) -> AnalysisResult:
    title = REPORTS[rid][0]
    res = AnalysisResult(
        analysis_id=rid, title=f"{title}: {station}", area_name=station, area_source="Davis Vantage Pro2 station",
        period_label=f"{start:%d %b %Y} – {end:%d %b %Y}", info_what=t(f"info.{rid}.what"),
        info_example=t(f"info.{rid}.example"), device_used="This PC (CPU)",
    )
    comp = daily["completeness_pct"].mean() if "completeness_pct" in daily and len(daily) else None
    if comp is not None and comp < 90:
        res.warnings.append(f"Data completeness is {comp:.0f}% for this period; totals may be too low.")
    res.method = {"datasets": ["Davis Vantage Pro2 records imported from WeatherLink, quality-checked"],
                  "device": "This PC (CPU)"}
    return res


def build(rid: str, daily: pd.DataFrame, records: pd.DataFrame, station: str, start: date, end: date,
          meta: dict[str, Any] | None = None) -> AnalysisResult:
    meta = meta or {}
    dd = daily[(daily["date"] >= pd.Timestamp(start)) & (daily["date"] <= pd.Timestamp(end))].copy()
    if dd.empty:
        raise ValueError("No station data in the chosen period.")
    dd["day"] = dd["date"].dt.strftime("%Y-%m-%d")
    fn: Callable = BUILDERS[rid]
    res = _base(rid, station, start, end, dd)
    fn(res, dd, records, meta)
    return res


# ---------------------------------------------------------------------------

def _temperature(res, dd, rec, meta):
    hot = dd.loc[dd["tmax_c"].idxmax()]
    cold = dd.loc[dd["tmin_c"].idxmin()]
    warm_nights = int((dd["tmin_c"] > meta.get("warm_night_c", 26)).sum())
    cold_nights = int((dd["tmin_c"] < meta.get("cold_night_c", 10)).sum())
    res.stats = pd.DataFrame([
        ("Highest temperature", f"{hot['tmax_c']:.1f} °C on {_fmt_day(hot['date'])}" + (f" at {hot['tmax_time']}" if hot.get('tmax_time') else "")),
        ("Lowest temperature", f"{cold['tmin_c']:.1f} °C on {_fmt_day(cold['date'])}"),
        ("Average temperature", f"{dd['tmean_c'].mean():.1f} °C"),
        ("Average daily high / low", f"{dd['tmax_c'].mean():.1f} / {dd['tmin_c'].mean():.1f} °C"),
        (f"Warm nights (low above {meta.get('warm_night_c', 26)} °C)", str(warm_nights)),
        (f"Cold nights (low below {meta.get('cold_night_c', 10)} °C)", str(cold_nights)),
        ("Days analysed", str(len(dd))),
    ], columns=["Measure", "Value"])
    res.charts = [ChartSpec("Daily high, average and low temperature", "band", dd, "day", ["tmax_c", "tmean_c", "tmin_c"],
                            y_title="°C", extra={"labels": ["High", "Average", "Low"]})]
    if len(dd) > 45:
        m = dd.groupby(dd["date"].dt.to_period("M")).agg(High=("tmax_c", "mean"), Low=("tmin_c", "mean"), Mean=("tmean_c", "mean")).round(1)
        m = m.reset_index().rename(columns={"date": "Month"})
        m["Month"] = m["Month"].astype(str)
        res.charts.append(ChartSpec("Monthly average high and low", "line", m, "Month", ["High", "Mean", "Low"], y_title="°C"))
    res.key_numbers = {"max_c": round(hot["tmax_c"], 1), "max_day": _fmt_day(hot["date"]), "min_c": round(cold["tmin_c"], 1),
                       "warm_nights": warm_nights, "mean_c": round(dd["tmean_c"].mean(), 1)}
    res.summary = (f"The highest temperature was {hot['tmax_c']:.1f} °C on {_fmt_day(hot['date'])} and the lowest {cold['tmin_c']:.1f} °C "
                   f"on {_fmt_day(cold['date'])}. The average was {dd['tmean_c'].mean():.1f} °C. {warm_nights} night(s) stayed above "
                   f"{meta.get('warm_night_c', 26)} °C.")
    res.method["formula"] = "Daily high and low from the station's high/low columns when present, otherwise from the records"


def _rainfall(res, dd, rec, meta):
    wet = meta.get("wet_mm", 1.0)
    total = dd["rain_mm"].sum()
    rainy = int((dd["rain_mm"] >= wet).sum())
    wet_run, wet_end = derive.runs(dd["rain_mm"] >= wet)
    dry_run, dry_end = derive.runs(dd["rain_mm"] < wet)
    big = dd.loc[dd["rain_mm"].idxmax()]
    h1 = dd.loc[dd["rain_max_1h_mm"].idxmax()] if "rain_max_1h_mm" in dd and dd["rain_max_1h_mm"].notna().any() else None
    rows = [("Total rain", f"{total:.1f} mm"), (f"Rainy days (≥ {wet:g} mm)", str(rainy)),
            ("Wettest day", f"{big['rain_mm']:.1f} mm on {_fmt_day(big['date'])}")]
    if h1 is not None:
        rows.append(("Heaviest one hour", f"{h1['rain_max_1h_mm']:.1f} mm on {_fmt_day(h1['date'])}"))
    if "rain_rate_max_mmh" in dd and dd["rain_rate_max_mmh"].notna().any():
        rows.append(("Highest rain rate", f"{dd['rain_rate_max_mmh'].max():.0f} mm/h"))
    if wet_run:
        rows.append(("Longest wet run", f"{wet_run} days, ending {_fmt_day(dd['date'].iloc[wet_end])}"))
    if dry_run:
        rows.append(("Longest dry run", f"{dry_run} days, ending {_fmt_day(dd['date'].iloc[dry_end])}"))
    res.stats = pd.DataFrame(rows, columns=["Measure", "Value"])
    res.charts = [ChartSpec("Daily rain", "bar", dd, "day", ["rain_mm"], y_title="mm")]
    if len(dd) > 45:
        m = dd.groupby(dd["date"].dt.to_period("M"))["rain_mm"].sum().round(1).reset_index()
        m["Month"] = m["date"].astype(str)
        res.charts.append(ChartSpec("Monthly rain", "bar", m[["Month", "rain_mm"]], "Month", ["rain_mm"], y_title="mm"))
    dd2 = dd.assign(cum=dd["rain_mm"].fillna(0).cumsum())
    res.charts.append(ChartSpec("Accumulated rain", "line", dd2, "day", ["cum"], y_title="mm"))
    res.key_numbers = {"total_mm": round(total, 1), "rainy_days": rainy, "wettest_day_mm": round(big["rain_mm"], 1),
                       "wettest_day": _fmt_day(big["date"]), "longest_dry_run_days": dry_run}
    res.summary = (f"{total:.0f} mm of rain fell on {rainy} rainy day(s). The wettest day was {_fmt_day(big['date'])} with "
                   f"{big['rain_mm']:.0f} mm" + (f"; the heaviest single hour gave {h1['rain_max_1h_mm']:.0f} mm" if h1 is not None else "")
                   + f". The longest dry run lasted {dry_run} day(s).")


def wind_rose_table(rec: pd.DataFrame, speed_col: str = "wind_kmh", dir_col: str = "wind_dir_deg") -> tuple[pd.DataFrame, float]:
    d = rec[[speed_col, dir_col]].dropna()
    if d.empty:
        return pd.DataFrame(), 0.0
    calm = (d[speed_col] < 1).mean() * 100
    d = d[d[speed_col] >= 1]
    sectors = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = (((d[dir_col] % 360) + 11.25) // 22.5).astype(int) % 16
    bins = [1, 5, 10, 20, 30, 1e9]
    labels = ["1–5", "5–10", "10–20", "20–30", "30+"]
    sb = pd.cut(d[speed_col], bins=bins, labels=labels, right=False)
    tab = pd.crosstab(idx.map(lambda i: sectors[i]), sb, normalize=False)
    tab = (100 * tab / max(1, len(rec[[speed_col, dir_col]].dropna()))).reindex(sectors).fillna(0).round(2)
    tab.index.name = "Direction"
    return tab.reset_index(), calm


def _wind(res, dd, rec, meta):
    r = rec.copy()
    r["ts_local"] = pd.to_datetime(r["ts_local"])
    r = r[(r["ts_local"] >= dd["date"].min()) & (r["ts_local"] < dd["date"].max() + pd.Timedelta(days=1))]
    rose, calm = wind_rose_table(r) if {"wind_kmh", "wind_dir_deg"} <= set(r) else (pd.DataFrame(), 0)
    g = dd.loc[dd["gust_max_kmh"].idxmax()] if "gust_max_kmh" in dd and dd["gust_max_kmh"].notna().any() else None
    rows = []
    if "wind_mean_kmh" in dd:
        rows.append(("Average wind speed", f"{dd['wind_mean_kmh'].mean():.1f} km/h"))
    if g is not None:
        rows.append(("Strongest gust", f"{g['gust_max_kmh']:.0f} km/h on {_fmt_day(g['date'])}" + (f" at {g['gust_time']}" if g.get("gust_time") else "")))
    prevailing = None
    if not rose.empty:
        tot = rose.set_index("Direction").sum(axis=1)
        prevailing = tot.idxmax()
        rows.append(("Most common direction (wind from)", f"{prevailing} ({tot.max():.0f}% of the time)"))
        rows.append(("Calm (below 1 km/h)", f"{calm:.0f}% of the time"))
    res.stats = pd.DataFrame(rows, columns=["Measure", "Value"])
    if not rose.empty:
        res.charts.append(ChartSpec("Wind rose (% of time, wind from)", "windrose", rose, "Direction",
                                    [c for c in rose.columns if c != "Direction"], y_title="% of time", extra={"calm_pct": round(calm, 1)}))
    cols = [c for c in ("wind_mean_kmh", "gust_max_kmh") if c in dd]
    res.charts.append(ChartSpec("Daily average wind and strongest gust", "line", dd, "day", cols, y_title="km/h"))
    res.key_numbers = {"mean_kmh": round(dd["wind_mean_kmh"].mean(), 1) if "wind_mean_kmh" in dd else None,
                       "max_gust_kmh": None if g is None else round(g["gust_max_kmh"], 0), "prevailing_from": prevailing}
    res.summary = ((f"Winds came mostly from the {prevailing}. " if prevailing else "")
                   + (f"The average speed was {dd['wind_mean_kmh'].mean():.0f} km/h. " if "wind_mean_kmh" in dd else "")
                   + (f"The strongest gust was {g['gust_max_kmh']:.0f} km/h on {_fmt_day(g['date'])}." if g is not None else ""))


def _humidity(res, dd, rec, meta):
    muggy_thr = meta.get("muggy_dew_c", 24)
    muggy = int((dd.get("dew_max_c", pd.Series(dtype=float)) > muggy_thr).sum())
    fog = int(((dd.get("rh_max", pd.Series(dtype=float)) >= 97) & (dd.get("wind_mean_kmh", pd.Series(5, index=dd.index)) < 5)).sum())
    res.stats = pd.DataFrame([
        ("Average humidity", f"{dd['rh_mean'].mean():.0f}%"),
        ("Average daily humidity range", f"{dd['rh_min'].mean():.0f}–{dd['rh_max'].mean():.0f}%"),
        ("Average dew point", f"{dd['dew_mean_c'].mean():.1f} °C" if "dew_mean_c" in dd else "n/a"),
        (f"Very muggy days (dew point above {muggy_thr} °C)", str(muggy)),
        ("Fog-risk nights (humidity ≥ 97%, light wind)", str(fog)),
    ], columns=["Measure", "Value"])
    res.charts = [ChartSpec("Daily humidity range", "band", dd, "day", ["rh_max", "rh_mean", "rh_min"], y_title="%",
                            extra={"labels": ["Max", "Average", "Min"]})]
    if "dew_mean_c" in dd:
        res.charts.append(ChartSpec("Daily dew point", "line", dd, "day", ["dew_mean_c", "dew_max_c"], y_title="°C",
                                    extra={"hlines": [(muggy_thr, "Very muggy")]}))
    res.key_numbers = {"mean_rh": round(dd["rh_mean"].mean(), 0), "muggy_days": muggy, "fog_risk_nights": fog}
    res.summary = (f"Humidity averaged {dd['rh_mean'].mean():.0f}%. The dew point went above {muggy_thr} °C (very muggy) on {muggy} day(s), "
                   f"and {fog} night(s) had fog-risk conditions.")


def _feelslike(res, dd, rec, meta):
    top = dd.loc[dd["heat_index_max_c"].idxmax()]
    caution_days = int((dd["heat_index_max_c"] >= 32).sum())
    danger_days = int((dd["heat_index_max_c"] >= 41).sum())
    rows = [("Highest heat index", f"{top['heat_index_max_c']:.0f} °C on {_fmt_day(top['date'])} ({derive.band(top['heat_index_max_c'], derive.HEAT_INDEX_BANDS)})"),
            ("Days reaching 'extreme caution' (32 °C+)", str(caution_days)),
            ("Days reaching 'danger' (41 °C+)", str(danger_days))]
    if "hi_danger_h" in dd:
        rows.append(("Hours in danger band", f"{dd['hi_danger_h'].sum():.0f}"))
    if "wind_chill_min_c" in dd and (dd["wind_chill_min_c"] < dd.get("tmin_c", dd["wind_chill_min_c"]) - 0.5).any():
        wc = dd.loc[dd["wind_chill_min_c"].idxmin()]
        rows.append(("Lowest wind chill", f"{wc['wind_chill_min_c']:.0f} °C on {_fmt_day(wc['date'])}"))
    res.stats = pd.DataFrame(rows, columns=["Measure", "Value"])
    cols = ["heat_index_max_c"] + (["tmax_c"] if "tmax_c" in dd else [])
    res.charts = [ChartSpec("Daily highest heat index and temperature", "line", dd, "day", cols, y_title="°C",
                            extra={"hlines": [(32, "Extreme caution"), (41, "Danger"), (54, "Extreme danger")]})]
    res.key_numbers = {"max_heat_index_c": round(top["heat_index_max_c"], 0), "danger_days": danger_days}
    res.summary = (f"The heat index (how hot it feels with humidity) peaked at {top['heat_index_max_c']:.0f} °C on {_fmt_day(top['date'])}. "
                   f"It reached the danger band (41 °C or more) on {danger_days} day(s).")
    res.method["formula"] = "Heat index: NOAA Rothfusz regression with NWS adjustments; wind chill: Environment Canada/NWS formula"


def _pressure(res, dd, rec, meta):
    thr = meta.get("pressure_drop_hpa", 4)
    drops = dd[dd["bar_drop_12h_hpa"] <= -thr] if "bar_drop_12h_hpa" in dd else pd.DataFrame()
    big = dd.loc[dd["bar_drop_12h_hpa"].idxmin()] if "bar_drop_12h_hpa" in dd and dd["bar_drop_12h_hpa"].notna().any() else None
    rows = [("Average pressure", f"{dd['bar_mean_hpa'].mean():.1f} hPa"),
            ("Range", f"{dd['bar_mean_hpa'].min():.1f}–{dd['bar_mean_hpa'].max():.1f} hPa (daily means)"),
            (f"Days with a drop of {thr} hPa or more within 12 h", str(len(drops)))]
    if big is not None:
        rows.append(("Biggest 12-hour drop", f"{-big['bar_drop_12h_hpa']:.1f} hPa on {_fmt_day(big['date'])}"))
    res.stats = pd.DataFrame(rows, columns=["Measure", "Value"])
    r = rec.copy()
    r["ts_local"] = pd.to_datetime(r["ts_local"])
    r = r[(r["ts_local"] >= dd["date"].min()) & (r["ts_local"] < dd["date"].max() + pd.Timedelta(days=1))]
    if "bar_hpa" in r and len(r) > 0:
        hourly = r.set_index("ts_local")["bar_hpa"].resample("h").mean().dropna().reset_index()
        hourly["time"] = hourly["ts_local"].dt.strftime("%Y-%m-%d %H:%M")
        res.charts.append(ChartSpec("Pressure (hourly)", "line", hourly, "time", ["bar_hpa"], y_title="hPa"))
    res.charts.append(ChartSpec("Largest 12-hour pressure drop each day", "bar", dd, "day", ["bar_drop_12h_hpa"], y_title="hPa",
                                extra={"signed": True, "hlines": [(-thr, "Storm-risk drop")]}))
    res.key_numbers = {"storm_drop_days": len(drops), "max_drop_hpa": None if big is None else round(-big["bar_drop_12h_hpa"], 1)}
    res.summary = (f"Pressure averaged {dd['bar_mean_hpa'].mean():.0f} hPa. "
                   + (f"The biggest fall was {-big['bar_drop_12h_hpa']:.1f} hPa within 12 hours on {_fmt_day(big['date'])}; quick falls like "
                      "this often come before storms. " if big is not None else "")
                   + f"{len(drops)} day(s) had a fall of {thr} hPa or more.")


def _solar(res, dd, rec, meta):
    m = dd.copy()
    m["month"] = m["date"].dt.to_period("M")
    ref = m.groupby("month")["solar_mj"].transform(lambda s: s.quantile(0.95))
    m["sky"] = np.where(m["solar_mj"] >= 0.75 * ref, "Clear or mostly clear",
                        np.where(m["solar_mj"] <= 0.35 * ref, "Cloudy", "Partly cloudy"))
    counts = m["sky"].value_counts().reindex(["Clear or mostly clear", "Partly cloudy", "Cloudy"]).fillna(0).astype(int)
    best = m.loc[m["solar_mj"].idxmax()]
    res.stats = pd.DataFrame([
        ("Average daily solar energy", f"{m['solar_mj'].mean():.1f} MJ/m²"),
        ("Total solar energy", f"{m['solar_mj'].sum():.0f} MJ/m² ({m['solar_mj'].sum() / 3.6:.0f} kWh/m²)"),
        ("Sunniest day", f"{best['solar_mj']:.1f} MJ/m² on {_fmt_day(best['date'])}"),
        ("Highest solar radiation", f"{m['solar_max_wm2'].max():.0f} W/m²"),
        *[(f"{k} days", str(v)) for k, v in counts.items()],
    ], columns=["Measure", "Value"])
    res.charts = [ChartSpec("Daily solar energy", "bar", m, "day", ["solar_mj"], y_title="MJ/m²")]
    if m["month"].nunique() > 1:
        mm = m.groupby("month")["solar_mj"].sum().round(0).reset_index()
        mm["Month"] = mm["month"].astype(str)
        res.charts.append(ChartSpec("Monthly solar energy", "bar", mm[["Month", "solar_mj"]], "Month", ["solar_mj"], y_title="MJ/m²"))
    res.key_numbers = {"mean_mj": round(m["solar_mj"].mean(), 1), "clear_days": int(counts.iloc[0]), "cloudy_days": int(counts.iloc[2])}
    res.summary = (f"The station received on average {m['solar_mj'].mean():.1f} MJ/m² of solar energy a day. There were {counts.iloc[0]} clear "
                   f"or mostly clear days and {counts.iloc[2]} cloudy days.")
    res.method["formula"] = "Daily energy = sum of solar radiation × record interval. Clear/cloudy compared with the month's sunniest days (95th percentile)"
    res.limitations = ["Clear/cloudy labels are estimates from the station's own sunniest days, not cloud observations."]


def _uv(res, dd, rec, meta):
    m = dd.copy()
    m["band"] = [derive.band(v, derive.UV_BANDS) for v in m["uv_max"]]
    counts = m["band"].value_counts().reindex([b for _, b in derive.UV_BANDS]).fillna(0).astype(int)
    top = m.loc[m["uv_max"].idxmax()]
    peak_times = m["uv_peak_time"].dropna() if "uv_peak_time" in m else pd.Series(dtype=str)
    typical_peak = peak_times.mode().iloc[0] if not peak_times.empty else None
    res.stats = pd.DataFrame([
        ("Highest UV index", f"{top['uv_max']:.1f} on {_fmt_day(top['date'])} ({derive.band(top['uv_max'], derive.UV_BANDS)})"),
        ("Typical time of daily peak", typical_peak or "n/a"),
        ("Hours at high UV or above (6+)", f"{m['uv_high_h'].sum():.0f}" if "uv_high_h" in m else "n/a"),
        ("Hours at extreme UV (11+)", f"{m['uv_extreme_h'].sum():.0f}" if "uv_extreme_h" in m else "n/a"),
        *[(f"Days peaking '{k}'", str(v)) for k, v in counts.items()],
    ], columns=["Measure", "Value"])
    res.charts = [ChartSpec("Daily peak UV index", "bar", m, "day", ["uv_max"], y_title="UV index",
                            extra={"hlines": [(3, "Moderate"), (6, "High"), (8, "Very high"), (11, "Extreme")]})]
    res.key_numbers = {"max_uv": round(top["uv_max"], 1), "extreme_days": int(counts.get("Extreme", 0))}
    res.summary = (f"UV peaked at {top['uv_max']:.0f} ({derive.band(top['uv_max'], derive.UV_BANDS).lower()}) on {_fmt_day(top['date'])}. "
                   + (f"The daily peak usually came around {typical_peak}. " if typical_peak else "")
                   + "On high-UV days, limit time in direct sun around midday and use shade, a hat and sunscreen.")
    res.method["formula"] = "UV bands from the WHO UV index scale: 0–2 low, 3–5 moderate, 6–7 high, 8–10 very high, 11+ extreme"


def _soil(res, dd, rec, meta):
    thr = meta.get("irrigation_threshold_cb", 60)
    cols = [c for c in ("soil_1_cb", "soil_2_cb", "soil_3_cb", "soil_4_cb") if c in dd and dd[c].notna().any()]
    depths = meta.get("soil_depths", {})
    rows, alerts = [], []
    for c in cols:
        n = c.split("_")[1]
        lab = f"Sensor {n}" + (f" ({depths[n]})" if n in depths else "")
        last = dd[c].dropna().iloc[-1]
        over = dd[dd[c] >= thr]
        rows.append((f"{lab}: latest", f"{last:.0f} cb ({derive.band(last, derive.SOIL_BANDS).lower()})"))
        rows.append((f"{lab}: days at or above {thr:g} cb", str(len(over))))
        if len(over):
            alerts.append(f"{lab} first crossed the irrigation threshold on {_fmt_day(over['date'].iloc[0])}")
    res.stats = pd.DataFrame(rows, columns=["Measure", "Value"])
    extra = {"hlines": [(thr, "Irrigation threshold")], "invert_note": "Higher centibars = drier soil"}
    res.charts = [ChartSpec("Soil water tension (higher = drier)", "line", dd, "day", cols, y_title="centibars", extra=extra)]
    if "rain_mm" in dd:
        res.charts.append(ChartSpec("Daily rain (for wetting events)", "bar", dd, "day", ["rain_mm"], y_title="mm"))
    res.key_numbers = {"threshold_cb": thr, **{f"{c}_latest": round(dd[c].dropna().iloc[-1], 0) for c in cols}}
    res.summary = ("Soil moisture is read as water tension: higher centibars mean drier soil. "
                   + (" ".join(a + "." for a in alerts) if alerts else f"No sensor reached the irrigation threshold of {thr:g} cb."))
    res.method["formula"] = (f"Daily mean soil water tension per sensor; irrigation threshold {thr:g} cb (editable in Settings > Station). "
                             "Bands: < 10 saturated, 10–30 adequately wet, 30–60 usual irrigation range, 60–100 dry, > 100 very dry (approximate)")
    res.limitations = ["The right irrigation threshold depends on soil type and crop; adjust it in Settings."]


def _et(res, dd, rec, meta):
    m = dd.copy()
    src = m["et_source"].dropna().iloc[0] if "et_source" in m and m["et_source"].notna().any() else "station"
    tot_et = m["et_mm"].sum()
    tot_rain = m["rain_mm"].sum() if "rain_mm" in m else None
    rows = [("Total evapotranspiration", f"{tot_et:.0f} mm ({src})"), ("Average per day", f"{m['et_mm'].mean():.1f} mm")]
    if tot_rain is not None:
        bal = tot_rain - tot_et
        rows += [("Total rain", f"{tot_rain:.0f} mm"), ("Water balance (rain − ET)", f"{bal:+.0f} mm ({'surplus' if bal >= 0 else 'deficit'})")]
    res.stats = pd.DataFrame(rows, columns=["Measure", "Value"])
    res.charts = [ChartSpec("Daily evapotranspiration", "bar", m, "day", ["et_mm"], y_title="mm")]
    if tot_rain is not None:
        m["Rain (cumulative)"] = m["rain_mm"].fillna(0).cumsum()
        m["ET (cumulative)"] = m["et_mm"].fillna(0).cumsum()
        res.charts.append(ChartSpec("Cumulative rain vs evapotranspiration", "line", m, "day", ["Rain (cumulative)", "ET (cumulative)"], y_title="mm"))
    res.key_numbers = {"et_total_mm": round(tot_et, 0), "rain_total_mm": tot_rain and round(tot_rain, 0),
                       "balance_mm": None if tot_rain is None else round(tot_rain - tot_et, 0)}
    res.summary = (f"Soil and plants lost about {tot_et:.0f} mm of water through evapotranspiration"
                   + (f", while rain added {tot_rain:.0f} mm: a {'surplus' if tot_rain >= tot_et else 'deficit'} of {abs(tot_rain - tot_et):.0f} mm."
                      if tot_rain is not None else "."))
    res.method["formula"] = ("ET from the station's own ET column (Davis computes it with the solar radiation sensor)" if src == "station"
                             else "FAO-56 Penman-Monteith reference ET from daily temperature, humidity, wind and solar radiation")


def _summary(res, dd, rec, meta):
    g = dd.groupby(dd["date"].dt.to_period("M"))
    agg = {}
    if "tmax_c" in dd:
        agg.update({"Mean high (°C)": ("tmax_c", "mean"), "Mean low (°C)": ("tmin_c", "mean"),
                    "Highest (°C)": ("tmax_c", "max"), "Lowest (°C)": ("tmin_c", "min")})
    if "rain_mm" in dd:
        agg.update({"Rain (mm)": ("rain_mm", "sum"), "Wettest day (mm)": ("rain_mm", "max")})
    if "rh_mean" in dd:
        agg["Humidity (%)"] = ("rh_mean", "mean")
    if "gust_max_kmh" in dd:
        agg["Strongest gust (km/h)"] = ("gust_max_kmh", "max")
    if "solar_mj" in dd:
        agg["Solar (MJ/m²)"] = ("solar_mj", "sum")
    if "et_mm" in dd:
        agg["ET (mm)"] = ("et_mm", "sum")
    if "completeness_pct" in dd:
        agg["Data complete (%)"] = ("completeness_pct", "mean")
    tab = g.agg(**agg).round(1).reset_index()
    tab["Month"] = tab["date"].astype(str)
    tab = tab.drop(columns="date")
    tab = tab[["Month"] + [c for c in tab.columns if c != "Month"]]
    res.stats = tab
    res.stats_title = "Monthly summary"
    if "Rain (mm)" in tab:
        res.charts.append(ChartSpec("Monthly rain", "bar", tab, "Month", ["Rain (mm)"], y_title="mm"))
    if "Mean high (°C)" in tab:
        res.charts.append(ChartSpec("Monthly mean high and low", "line", tab, "Month", ["Mean high (°C)", "Mean low (°C)"], y_title="°C"))
    hot = dd.loc[dd["tmax_c"].idxmax()] if "tmax_c" in dd else None
    wet = dd.loc[dd["rain_mm"].idxmax()] if "rain_mm" in dd else None
    res.key_numbers = {"months": len(tab), "total_rain_mm": round(dd["rain_mm"].sum(), 0) if "rain_mm" in dd else None,
                       "max_temp_c": None if hot is None else round(hot["tmax_c"], 1)}
    res.summary = (f"Summary of {len(tab)} month(s). "
                   + (f"Total rain {dd['rain_mm'].sum():.0f} mm; wettest day {_fmt_day(wet['date'])} ({wet['rain_mm']:.0f} mm). " if wet is not None else "")
                   + (f"Highest temperature {hot['tmax_c']:.1f} °C on {_fmt_day(hot['date'])}." if hot is not None else ""))


BUILDERS: dict[str, Callable] = {
    "station_temperature": _temperature, "station_rainfall": _rainfall, "station_wind": _wind,
    "station_humidity": _humidity, "station_feelslike": _feelslike, "station_pressure": _pressure,
    "station_solar": _solar, "station_uv": _uv, "station_soil": _soil, "station_et": _et, "station_summary": _summary,
}


def period_bounds(kind: str, today: date | None = None) -> tuple[date, date]:
    """Ready-made report ranges."""
    from dateutil.relativedelta import relativedelta

    today = today or date.today()
    if kind == "daily":
        return today, today
    if kind == "weekly":
        return today - pd.Timedelta(days=6).to_pytimedelta(), today
    if kind == "monthly":
        first = today.replace(day=1)
        prev = first - relativedelta(months=1)
        return prev, first - pd.Timedelta(days=1).to_pytimedelta()
    if kind == "seasonal":
        if today.month >= 10 or today.month <= 2:  # report the monsoon just ended / or the winter so far
            y = today.year if today.month >= 10 else today.year - 1
            return date(y, 6, 1), date(y, 9, 30)
        return date(today.year - 1, 12, 1), date(today.year, 2, 28)
    raise ValueError(kind)
