"""Derived values and daily aggregation for station data (all metric)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# point formulas
# ---------------------------------------------------------------------------

def dew_point_c(t_c, rh):
    """Magnus formula (Alduchov & Eskridge 1996 constants)."""
    t = np.asarray(t_c, dtype=float)
    rh = np.clip(np.asarray(rh, dtype=float), 1e-3, 100)
    a, b = 17.625, 243.04
    g = np.log(rh / 100) + a * t / (b + t)
    return b * g / (a - g)


def heat_index_c(t_c, rh):
    """NOAA heat index (Rothfusz regression with the NWS adjustments). Below 26.7 °C returns the simple formula."""
    t = np.asarray(t_c, dtype=float) * 9 / 5 + 32
    r = np.asarray(rh, dtype=float)
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + r * 0.094)
    full = (-42.379 + 2.04901523 * t + 10.14333127 * r - 0.22475541 * t * r - 6.83783e-3 * t * t
            - 5.481717e-2 * r * r + 1.22874e-3 * t * t * r + 8.5282e-4 * t * r * r - 1.99e-6 * t * t * r * r)
    adj1 = np.where((r < 13) & (t >= 80) & (t <= 112), ((13 - r) / 4) * np.sqrt(np.clip((17 - np.abs(t - 95)) / 17, 0, None)), 0)
    adj2 = np.where((r > 85) & (t >= 80) & (t <= 87), ((r - 85) / 10) * ((87 - t) / 5), 0)
    hi_f = np.where((simple + t) / 2 >= 80, full - adj1 + adj2, simple)
    return (hi_f - 32) * 5 / 9


def wind_chill_c(t_c, v_kmh):
    """Environment Canada / NWS wind chill; only meaningful at or below 10 °C and above 4.8 km/h."""
    t = np.asarray(t_c, dtype=float)
    v = np.asarray(v_kmh, dtype=float)
    wc = 13.12 + 0.6215 * t - 11.37 * np.power(np.clip(v, 0, None), 0.16) + 0.3965 * t * np.power(np.clip(v, 0, None), 0.16)
    return np.where((t <= 10) & (v > 4.8), wc, t)


HEAT_INDEX_BANDS = [  # NWS categories in °C
    (27, "Normal"), (32, "Caution"), (41, "Extreme caution"), (54, "Danger"), (1e9, "Extreme danger")]
UV_BANDS = [(3, "Low"), (6, "Moderate"), (8, "High"), (11, "Very high"), (1e9, "Extreme")]  # WHO
SOIL_BANDS = [  # centibars (soil water tension, Watermark-type sensors) - approximate guidance
    (10, "Saturated"), (30, "Adequately wet"), (60, "Usual irrigation range"), (100, "Dry"), (1e9, "Very dry")]


def band(v: float, bands) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "No data"
    for b, lab in bands:
        if v < b:
            return lab
    return bands[-1][1]


# ---------------------------------------------------------------------------
# FAO-56 Penman-Monteith reference ET (daily)
# ---------------------------------------------------------------------------

def fao56_et0(tmax, tmin, rh_mean, u2_ms, rs_mj, lat_deg, elev_m, doy):
    """Daily reference evapotranspiration ET0 (mm/day), FAO-56 eq. 6.

    rs_mj: incoming solar radiation, MJ m-2 day-1; u2_ms: wind speed at 2 m (m/s).
    """
    tmax, tmin, rh_mean, u2, rs, doy = (np.asarray(x, dtype=float) for x in (tmax, tmin, rh_mean, u2_ms, rs_mj, doy))
    tmean = (tmax + tmin) / 2
    p = 101.3 * ((293 - 0.0065 * elev_m) / 293) ** 5.26
    gamma = 0.000665 * p
    def es(t):
        return 0.6108 * np.exp(17.27 * t / (t + 237.3))
    es_ = (es(tmax) + es(tmin)) / 2
    ea = es_ * rh_mean / 100
    delta = 4098 * es(tmean) / (tmean + 237.3) ** 2
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * np.cos(2 * np.pi * doy / 365)
    dec = 0.409 * np.sin(2 * np.pi * doy / 365 - 1.39)
    ws = np.arccos(np.clip(-np.tan(phi) * np.tan(dec), -1, 1))
    ra = 24 * 60 / np.pi * 0.0820 * dr * (ws * np.sin(phi) * np.sin(dec) + np.cos(phi) * np.cos(dec) * np.sin(ws))
    rso = (0.75 + 2e-5 * elev_m) * ra
    rns = 0.77 * rs
    sigma = 4.903e-9
    rnl = sigma * ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2 * (0.34 - 0.14 * np.sqrt(np.clip(ea, 0, None))) * \
        (1.35 * np.clip(rs / np.where(rso > 0, rso, np.nan), 0, 1) - 0.35)
    rn = rns - rnl
    et0 = (0.408 * delta * rn + gamma * 900 / (tmean + 273) * u2 * (es_ - ea)) / (delta + gamma * (1 + 0.34 * u2))
    return np.clip(et0, 0, None)


def wind_to_2m(u_ms, height_m: float = 10.0):
    """FAO-56 eq. 47 log profile; Davis anemometers are often mounted around 2–10 m."""
    return np.asarray(u_ms, dtype=float) * 4.87 / math.log(67.8 * height_m - 5.42)


# ---------------------------------------------------------------------------
# record-level enrichment and daily table
# ---------------------------------------------------------------------------

def enrich(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    if "dew_c" not in d or d["dew_c"].isna().all():
        if {"temp_c", "hum_pct"} <= set(d):
            d["dew_c"] = dew_point_c(d["temp_c"], d["hum_pct"])
    if {"temp_c", "hum_pct"} <= set(d) and ("heat_index_c" not in d or d["heat_index_c"].isna().all()):
        d["heat_index_c"] = heat_index_c(d["temp_c"], d["hum_pct"])
    if {"temp_c", "wind_kmh"} <= set(d) and ("wind_chill_c" not in d or d["wind_chill_c"].isna().all()):
        d["wind_chill_c"] = wind_chill_c(d["temp_c"], d["wind_kmh"])
    return d


def daily(df: pd.DataFrame, lat: float | None = None, elev_m: float | None = None, anemometer_m: float = 10.0,
          wet_mm: float = 1.0) -> pd.DataFrame:
    """One row per local day with the values the reports need."""
    d = enrich(df)
    d["ts_local"] = pd.to_datetime(d["ts_local"])
    d = d.set_index("ts_local").sort_index()
    interval_h = (d["interval_min"].median() / 60) if "interval_min" in d and d["interval_min"].notna().any() else \
        d.index.to_series().diff().dt.total_seconds().median() / 3600
    g = d.resample("D")
    out = pd.DataFrame(index=g.size().index)
    out["records"] = g.size()
    def has(c):
        return c in d and d[c].notna().any()
    if has("temp_c"):
        out["tmean_c"] = g["temp_c"].mean()
        out["tmax_c"] = (g["temp_hi_c"].max() if has("temp_hi_c") else g["temp_c"].max())
        out["tmin_c"] = (g["temp_lo_c"].min() if has("temp_lo_c") else g["temp_c"].min())
        out["tmax_time"] = g["temp_c"].apply(lambda s: s.idxmax().strftime("%H:%M") if s.notna().any() else None)
    if has("hum_pct"):
        out["rh_mean"] = g["hum_pct"].mean()
        out["rh_max"] = g["hum_pct"].max()
        out["rh_min"] = g["hum_pct"].min()
    if has("dew_c"):
        out["dew_mean_c"] = g["dew_c"].mean()
        out["dew_max_c"] = g["dew_c"].max()
    if has("heat_index_c"):
        out["heat_index_max_c"] = g["heat_index_c"].max()
        hi = d["heat_index_c"]
        out["hi_caution_h"] = (hi >= 27).resample("D").sum() * interval_h
        out["hi_danger_h"] = (hi >= 41).resample("D").sum() * interval_h
    if has("wind_chill_c"):
        out["wind_chill_min_c"] = g["wind_chill_c"].min()
    if has("rain_mm"):
        out["rain_mm"] = g["rain_mm"].sum(min_count=1)
        out["rain_max_1h_mm"] = d["rain_mm"].resample("h").sum(min_count=1).resample("D").max()
    if has("rain_rate_mmh"):
        out["rain_rate_max_mmh"] = g["rain_rate_mmh"].max()
    if has("wind_kmh"):
        out["wind_mean_kmh"] = g["wind_kmh"].mean()
    gust_col = "gust_kmh" if has("gust_kmh") else ("wind_kmh" if has("wind_kmh") else None)
    if gust_col:
        out["gust_max_kmh"] = g[gust_col].max()
        out["gust_time"] = g[gust_col].apply(lambda s: s.idxmax().strftime("%H:%M") if s.notna().any() else None)
    if has("bar_hpa"):
        out["bar_mean_hpa"] = g["bar_hpa"].mean()
        roll_drop = d["bar_hpa"] - d["bar_hpa"].rolling("12h").max()
        out["bar_drop_12h_hpa"] = roll_drop.resample("D").min()
    if has("solar_wm2"):
        out["solar_mj"] = g["solar_wm2"].sum() * interval_h * 3600 / 1e6
        out["solar_max_wm2"] = (g["solar_hi_wm2"].max() if has("solar_hi_wm2") else g["solar_wm2"].max())
    if has("uv_index"):
        out["uv_max"] = (g["uv_hi"].max() if has("uv_hi") else g["uv_index"].max())
        uv = d["uv_index"]
        out["uv_high_h"] = (uv >= 6).resample("D").sum() * interval_h
        out["uv_extreme_h"] = (uv >= 11).resample("D").sum() * interval_h
        out["uv_peak_time"] = d["uv_index"].resample("D").apply(lambda s: s.idxmax().strftime("%H:%M") if s.notna().any() and s.max() > 0 else None)
    for i in range(1, 5):
        c = f"soil_cb_{i}"
        if has(c):
            out[f"soil_{i}_cb"] = g[c].mean()
    if has("et_mm"):
        out["et_mm"] = g["et_mm"].sum(min_count=1)
        out["et_source"] = "station"
    elif {"tmax_c", "tmin_c", "rh_mean", "wind_mean_kmh", "solar_mj"} <= set(out) and lat is not None:
        u2 = wind_to_2m(out["wind_mean_kmh"] / 3.6, anemometer_m)
        out["et_mm"] = fao56_et0(out["tmax_c"], out["tmin_c"], out["rh_mean"], u2, out["solar_mj"], lat, elev_m or 0,
                                 out.index.dayofyear)
        out["et_source"] = "FAO-56 from station data"
    if "rain_mm" in out and "et_mm" in out:
        out["water_balance_mm"] = out["rain_mm"].fillna(0) - out["et_mm"]
    # Completeness: share of expected records present.
    expected = 24 / interval_h if interval_h and interval_h == interval_h else np.nan
    out["completeness_pct"] = (100 * out["records"] / expected).clip(upper=100).round(0)
    out.index.name = "date"
    numeric = out.select_dtypes("number").columns
    out[numeric] = out[numeric].round(2)
    return out.reset_index()


def runs(flags: pd.Series) -> tuple[int, int]:
    """Longest run of True and the index where it ends."""
    best = cur = 0
    end = -1
    for i, v in enumerate(flags.fillna(False)):
        cur = cur + 1 if v else 0
        if cur > best:
            best, end = cur, i
    return best, end
