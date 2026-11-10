"""Quality control for station records before analysis."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Plausible ranges (metric). Generous, to catch only clearly broken readings.
RANGES = {
    "temp_c": (-40, 60), "temp_hi_c": (-40, 60), "temp_lo_c": (-40, 60), "dew_c": (-50, 40), "hum_pct": (0, 100),
    "wind_kmh": (0, 250), "gust_kmh": (0, 350), "wind_dir_deg": (0, 360), "gust_dir_deg": (0, 360),
    "bar_hpa": (870, 1085), "rain_mm": (0, 200), "rain_rate_mmh": (0, 1000), "solar_wm2": (0, 1800),
    "solar_hi_wm2": (0, 1800), "uv_index": (0, 20), "uv_hi": (0, 20), "et_mm": (0, 5),
    "soil_cb_1": (0, 200), "soil_cb_2": (0, 200), "soil_cb_3": (0, 200), "soil_cb_4": (0, 200),
    "heat_index_c": (-40, 75), "wind_chill_c": (-60, 60),
}
# Max believable change between consecutive records, per hour (spikes).
SPIKE_PER_HOUR = {"temp_c": 10.0, "hum_pct": 50.0, "bar_hpa": 8.0}
# A sensor that never changes for this many hours is probably stuck (zero wind/rain/solar excluded).
STUCK_HOURS = {"temp_c": 6, "hum_pct": 12, "bar_hpa": 12}


@dataclass
class QCReport:
    records: int
    start: str
    end: str
    interval_min: float
    gaps: pd.DataFrame          # start, end, missing_records
    flags: pd.DataFrame         # ts_utc, field, problem, value
    duplicates: int = 0

    @property
    def flagged_records(self) -> int:
        return self.flags["ts_utc"].nunique() if not self.flags.empty else 0

    def summary_rows(self) -> pd.DataFrame:
        rows = [
            ("Records", f"{self.records:,}"),
            ("From", self.start), ("To", self.end),
            ("Record interval", f"{self.interval_min:g} min" if self.interval_min == self.interval_min else "unknown"),
            ("Gaps (missing stretches)", f"{len(self.gaps)} ({int(self.gaps['missing_records'].sum()) if not self.gaps.empty else 0:,} records missing)"),
            ("Records with a flagged value", f"{self.flagged_records:,}"),
        ]
        if not self.flags.empty:
            for prob, n in self.flags.groupby("problem").size().items():
                rows.append((f"  {prob}", f"{n:,}"))
        return pd.DataFrame(rows, columns=["Check", "Result"])


def run(df: pd.DataFrame) -> QCReport:
    """df: canonical records with ts_local, ts_utc, interval_min and variable columns."""
    if df.empty:
        return QCReport(0, "", "", float("nan"), pd.DataFrame(columns=["start", "end", "missing_records"]),
                        pd.DataFrame(columns=["ts_utc", "field", "problem", "value"]))
    d = df.sort_values("ts_utc").reset_index(drop=True)
    t = pd.to_datetime(d["ts_utc"], utc=True)
    interval = float(d["interval_min"].median()) if "interval_min" in d and d["interval_min"].notna().any() else float(t.diff().dt.total_seconds().div(60).median())
    flags = []

    # Out of range
    for f, (lo, hi) in RANGES.items():
        if f in d:
            v = d[f]
            bad = v.notna() & ((v < lo) | (v > hi))
            for i in d.index[bad]:
                flags.append((d.at[i, "ts_utc"], f, "Out of range", v[i]))
    # Humidity / dew sanity
    if {"temp_c", "dew_c"} <= set(d.columns):
        bad = d["dew_c"] > d["temp_c"] + 0.5
        for i in d.index[bad.fillna(False)]:
            flags.append((d.at[i, "ts_utc"], "dew_c", "Dew point above temperature", d.at[i, "dew_c"]))
    # Spikes
    hours = t.diff().dt.total_seconds().div(3600)
    for f, lim in SPIKE_PER_HOUR.items():
        if f in d:
            jump = d[f].diff().abs()
            rate = jump / hours.clip(lower=interval / 60 if interval == interval else 1 / 12)
            back = d[f].diff(-1).abs()
            spike = (rate > lim) & (back > lim * (interval / 60 if interval == interval else 1)) & (jump > 3)
            for i in d.index[spike.fillna(False)]:
                flags.append((d.at[i, "ts_utc"], f, "Sudden spike", d.at[i, f]))
    # Stuck sensors
    if interval == interval and interval > 0:
        for f, hrs in STUCK_HOURS.items():
            if f not in d:
                continue
            n = int(hrs * 60 / interval)
            if n < 3:
                continue
            same = d[f].diff().eq(0)
            run_len = same.groupby((~same).cumsum()).cumsum()
            stuck = run_len >= n
            for i in d.index[stuck]:
                flags.append((d.at[i, "ts_utc"], f, "Sensor stuck (no change)", d.at[i, f]))
    flag_df = pd.DataFrame(flags, columns=["ts_utc", "field", "problem", "value"]).drop_duplicates(["ts_utc", "field", "problem"])

    # Gaps
    gaps = []
    if interval == interval and interval > 0:
        step = t.diff().dt.total_seconds().div(60)
        for i in d.index[step > 2.5 * interval]:
            missing = int(round(step[i] / interval)) - 1
            gaps.append((d.at[i - 1, "ts_local"], d.at[i, "ts_local"], missing))
    gap_df = pd.DataFrame(gaps, columns=["start", "end", "missing_records"])
    return QCReport(len(d), str(d["ts_local"].iloc[0]), str(d["ts_local"].iloc[-1]), interval, gap_df, flag_df)


def apply_exclusions(df: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    """Blank out flagged values (keeps the rest of each record)."""
    if flags.empty:
        return df
    d = df.copy()
    linked = {"temp_c": ["temp_hi_c", "temp_lo_c", "heat_index_c", "wind_chill_c", "dew_c"],
              "hum_pct": ["dew_c", "heat_index_c"], "wind_kmh": ["wind_chill_c"]}
    for f, grp in flags.groupby("field"):
        for col in [f] + linked.get(f, []):
            if col in d:
                d.loc[d["ts_utc"].isin(grp["ts_utc"]), col] = np.nan
    return d
