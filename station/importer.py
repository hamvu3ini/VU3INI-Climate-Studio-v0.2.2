"""Import Davis Vantage Pro2 records exported from WeatherLink.

Handles:
  * WeatherLink desktop "Export Records" text files: tab-separated, two header
    rows ("Temp" / "Out"), 12-hour times like "12:30a", "---" for missing values,
    compass wind directions, units as set in the software (not in the header).
  * WeatherLink.com CSV downloads: one header row with units in it
    ("Temp - °C", "Barometer - mb"), possibly a few lines of text above it.
  * CSV converted from .wlk archive files with the wlkReader tool.

The column-mapping wizard in the UI shows `guess_mapping()` to the user, who
confirms or corrects it; the confirmed mapping is saved per station and file
layout (signature) so later imports are automatic.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

# Canonical fields stored in the database (all metric).
FIELDS = {
    "temp_c": "Outside temperature", "temp_hi_c": "High temperature", "temp_lo_c": "Low temperature",
    "hum_pct": "Outside humidity", "dew_c": "Dew point", "wind_kmh": "Wind speed", "wind_dir_deg": "Wind direction",
    "gust_kmh": "High wind speed (gust)", "gust_dir_deg": "High wind direction", "wind_chill_c": "Wind chill",
    "heat_index_c": "Heat index", "bar_hpa": "Barometer", "rain_mm": "Rain", "rain_rate_mmh": "Rain rate",
    "solar_wm2": "Solar radiation", "solar_hi_wm2": "High solar radiation", "uv_index": "UV index", "uv_hi": "High UV index",
    "et_mm": "Evapotranspiration (ET)", "soil_cb_1": "Soil moisture 1", "soil_cb_2": "Soil moisture 2",
    "soil_cb_3": "Soil moisture 3", "soil_cb_4": "Soil moisture 4", "soil_temp_c_1": "Soil temperature 1",
    "soil_temp_c_2": "Soil temperature 2", "leaf_wet_1": "Leaf wetness", "in_temp_c": "Inside temperature",
    "in_hum_pct": "Inside humidity",
}
SPECIAL = {"datetime": "Date and time", "date": "Date", "time": "Time", "interval": "Archive interval (min)", "skip": "(ignore)"}

UNIT_CHOICES = {
    "temp": ["C", "F"],
    "wind": ["km/h", "m/s", "mph", "knots"],
    "rain": ["mm", "in"],
    "bar": ["hPa", "inHg", "mmHg"],
}
FIELD_UNIT_KIND = {
    "temp_c": "temp", "temp_hi_c": "temp", "temp_lo_c": "temp", "dew_c": "temp", "wind_chill_c": "temp",
    "heat_index_c": "temp", "in_temp_c": "temp", "soil_temp_c_1": "temp", "soil_temp_c_2": "temp",
    "wind_kmh": "wind", "gust_kmh": "wind", "rain_mm": "rain", "rain_rate_mmh": "rain", "et_mm": "rain", "bar_hpa": "bar",
}

# Ordered rules: first match wins. Patterns run on a normalised header (lowercase, single spaces).
RULES: list[tuple[str, str]] = [
    (r"^(date ?& ?time|date/time|datetime|timestamp|date time)\b", "datetime"),
    (r"^date$", "date"),
    (r"^time$", "time"),
    (r"arc\.? ?int|archive int", "interval"),
    (r"soil.*temp\D*(\d)?", "soil_temp_c_{n}"),
    (r"(soil.*moist|moist.*soil|soil ?mst|^moist)\D*(\d)?", "soil_cb_{n}"),
    (r"leaf.*wet", "leaf_wet_1"),
    (r"^(in|inside) ?(temp)", "in_temp_c"),
    (r"^(in|inside) ?(hum)", "in_hum_pct"),
    (r"^in ", "skip"),
    (r"(hi|high) ?(out )?temp", "temp_hi_c"),
    (r"(low|lo) ?(out )?temp", "temp_lo_c"),
    (r"thw|thsw|wet bulb|heat d-d|cool d-d|degree", "skip"),
    (r"(temp out|^temp\b|^outside temp|^out temp|^temperature)", "temp_c"),
    (r"(out hum|^hum|^outside hum|^humidity)", "hum_pct"),
    (r"dew", "dew_c"),
    (r"wind chill|^chill", "wind_chill_c"),
    (r"heat index|^heat ?idx", "heat_index_c"),
    (r"wind run|wind samp|wind tx|iss recept|recept", "skip"),
    (r"(hi|high) ?(wind )?(speed)", "gust_kmh"),
    (r"(hi|high) ?(wind )?(dir)", "gust_dir_deg"),
    (r"(wind speed|^speed|avg wind)", "wind_kmh"),
    (r"(wind dir|^dir|prevailing)", "wind_dir_deg"),
    (r"^(bar|barometer|pressure)", "bar_hpa"),
    (r"rain rate|^rate", "rain_rate_mmh"),
    (r"^rain|precip", "rain_mm"),
    (r"(hi|high) ?solar", "solar_hi_wm2"),
    (r"solar energy|^energy", "skip"),
    (r"solar", "solar_wm2"),
    (r"uv dose|^dose", "skip"),
    (r"(hi|high) ?uv", "uv_hi"),
    (r"uv", "uv_index"),
    (r"\bet\b|evapotrans", "et_mm"),
]

COMPASS = {d: i * 22.5 for i, d in enumerate(
    ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"])}
MISSING = {"---", "--", "-", "", "n/a", "na", "nan", "--.-", "---.-", "null"}


@dataclass
class RawFile:
    path: str
    kind: str                       # "desktop" | "csv"
    headers: list[str]
    data: pd.DataFrame              # raw text values, columns = headers (deduplicated)
    delimiter: str
    preview: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def signature(self) -> str:
        return hashlib.sha1("|".join(self.headers).encode()).hexdigest()[:16]


@dataclass
class Mapping:
    columns: dict[str, str]                 # header -> canonical field / special
    units: dict[str, str]                   # unit kind -> unit
    date_order: str = "DMY"                 # DMY | MDY | YMD
    timezone: str = "Asia/Kolkata"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Mapping":
        return cls(**d)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            txt = raw.decode(enc)
            if enc == "utf-16" and "\x00" in txt:
                continue
            return txt
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _norm(h: str) -> str:
    h = h.replace("°", "°").strip().lower()
    return re.sub(r"\s+", " ", h)


def _looks_like_header(cells: list[str]) -> bool:
    low = [c.strip().lower() for c in cells]
    return any("date" in c for c in low) and sum(1 for c in low if re.search(r"[a-z]", c)) >= max(3, len(low) // 2)


def read_file(path: str | Path) -> RawFile:
    p = Path(path)
    text = _read_text(p).replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        raise ValueError("The file is empty.")
    delim = "\t" if lines[0].count("\t") + (lines[1].count("\t") if len(lines) > 1 else 0) >= 4 else ","
    if delim == "," and lines[0].count(";") > lines[0].count(","):
        delim = ";"
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))

    # Desktop export: two header rows, the second starting with "Date", "Time".
    hdr_idx = next((i for i, r in enumerate(rows[:30]) if _looks_like_header(r)), None)
    if hdr_idx is None:
        raise ValueError("Could not find a header row with a Date column. Is this a WeatherLink export?")
    second = rows[hdr_idx]
    first = rows[hdr_idx - 1] if hdr_idx > 0 else []
    two_row = (
        hdr_idx > 0 and [c.strip().lower() for c in second[:2]] == ["date", "time"]
        and len(first) >= len(second) - 2 and sum(1 for c in first if c.strip()) >= 3
    )
    if two_row:
        first = first + [""] * (len(second) - len(first))
        headers = [f"{a.strip()} {b.strip()}".strip() for a, b in zip(first, second)]
        kind = "desktop"
    else:
        headers = [c.strip() for c in second]
        kind = "csv"
    # Deduplicate header names.
    seen: dict[str, int] = {}
    uniq = []
    for h in headers:
        h = h or "unnamed"
        if h in seen:
            seen[h] += 1
            uniq.append(f"{h} ({seen[h]})")
        else:
            seen[h] = 0
            uniq.append(h)
    body = [r + [""] * (len(uniq) - len(r)) if len(r) < len(uniq) else r[:len(uniq)] for r in rows[hdr_idx + 1:]]
    body = [r for r in body if any(c.strip() for c in r)]
    df = pd.DataFrame(body, columns=uniq)
    return RawFile(str(p), kind, uniq, df, delim, df.head(8))


# ---------------------------------------------------------------------------
# mapping guess
# ---------------------------------------------------------------------------

def _match(h: str) -> str:
    n = _norm(h)
    # Strip units like "- °c" or "(mm)" before matching.
    base = re.sub(r"\s*[-(\[].*$", "", n) if re.search(r" - |\(|\[", n) else n
    for pat, fld in RULES:
        m = re.search(pat, base)
        if m:
            if "{n}" in fld:
                num = next((g for g in m.groups()[::-1] if g and g.isdigit()), None) or "1"
                limit = 4 if fld.startswith("soil_cb") else 2
                return fld.replace("{n}", num if 1 <= int(num) <= limit else "1")
            return fld
    return "skip"


def _unit_from_header(h: str, kind: str) -> str | None:
    n = _norm(h)
    if kind == "temp":
        if "°f" in n or re.search(r"\bf\)?$", n) or "deg f" in n:
            return "F"
        if "°c" in n or re.search(r"\bc\)?$", n) or "deg c" in n:
            return "C"
    if kind == "wind":
        for u, pats in {"km/h": ["km/h", "kph", "kmh"], "m/s": ["m/s"], "mph": ["mph"], "knots": ["knot", "kts", " kt"]}.items():
            if any(x in n for x in pats):
                return u
    if kind == "rain":
        if re.search(r"\bmm\b|mm/h", n):
            return "mm"
        if re.search(r"\bin\b|in/h|inch", n):
            return "in"
    if kind == "bar":
        if "inhg" in n or re.search(r"\bin\b", n):
            return "inHg"
        if "mmhg" in n:
            return "mmHg"
        if "mb" in n or "hpa" in n or "mbar" in n:
            return "hPa"
    return None


def _num(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    s = s.where(~s.str.lower().isin(MISSING))
    return pd.to_numeric(s.str.replace(",", ".", regex=False) if s.str.contains(r"^\-?\d+,\d+$", na=False).mean() > 0.5 else s, errors="coerce")


def guess_mapping(raw: RawFile, timezone: str = "Asia/Kolkata", default_date_order: str = "DMY") -> Mapping:
    cols = {h: _match(h) for h in raw.headers}
    # Only one column per canonical field: keep the first.
    used: set[str] = set()
    for h, f in cols.items():
        if f in FIELDS or f in ("datetime", "date", "time", "interval"):
            if f in used:
                cols[h] = "skip"
            used.add(f)
    units: dict[str, str] = {}
    notes: list[str] = []
    for kind in UNIT_CHOICES:
        for h, f in cols.items():
            if FIELD_UNIT_KIND.get(f) == kind:
                u = _unit_from_header(h, kind)
                if u:
                    units[kind] = u
                    break
    # Value-based guesses where the header has no unit.
    temp_col = next((h for h, f in cols.items() if f == "temp_c"), None)
    if "temp" not in units and temp_col:
        med = _num(raw.data[temp_col]).median()
        units["temp"] = "F" if med is not None and med > 45 else "C"
        notes.append(f"Temperature unit guessed as °{units['temp']} from the values (median {med:.1f}).")
    bar_col = next((h for h, f in cols.items() if f == "bar_hpa"), None)
    if "bar" not in units and bar_col:
        med = _num(raw.data[bar_col]).median()
        units["bar"] = "inHg" if med and med < 40 else "mmHg" if med and 600 < med < 800 else "hPa"
        notes.append(f"Barometer unit guessed as {units['bar']} from the values.")
    imperial = units.get("temp") == "F"
    units.setdefault("wind", "mph" if imperial else "km/h")
    units.setdefault("rain", "in" if imperial else "mm")
    if raw.kind == "desktop":
        notes.append("WeatherLink desktop files do not include wind and rain units: please confirm them.")
    # Date order from the values.
    date_col = next((h for h, f in cols.items() if f in ("date", "datetime")), None)
    order = default_date_order
    if date_col:
        order = detect_date_order(raw.data[date_col], default_date_order)
    return Mapping(columns=cols, units=units, date_order=order, timezone=timezone, notes=notes)


def detect_date_order(s: pd.Series, default: str = "DMY") -> str:
    parts = s.astype(str).str.extract(r"^\s*(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})")
    parts = parts.dropna().astype(int)
    if parts.empty:
        return default
    if (parts[0] > 31).any():
        return "YMD"
    if (parts[0] > 12).any():
        return "DMY"
    if (parts[1] > 12).any():
        return "MDY"
    return default


# ---------------------------------------------------------------------------
# conversion to canonical metric records
# ---------------------------------------------------------------------------

def _to_c(v: pd.Series, unit: str) -> pd.Series:
    return (v - 32) * 5 / 9 if unit == "F" else v


def _to_kmh(v: pd.Series, unit: str) -> pd.Series:
    return v * {"km/h": 1, "m/s": 3.6, "mph": 1.609344, "knots": 1.852}[unit]


def _to_mm(v: pd.Series, unit: str) -> pd.Series:
    return v * 25.4 if unit == "in" else v


def _to_hpa(v: pd.Series, unit: str) -> pd.Series:
    return v * {"hPa": 1, "inHg": 33.8639, "mmHg": 1.33322}[unit]


def _parse_time(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip().str.lower()
    s = s.str.replace(r"^(\d{1,2}:\d{2}(:\d{2})?)\s*a$", r"\1 AM", regex=True)
    s = s.str.replace(r"^(\d{1,2}:\d{2}(:\d{2})?)\s*p$", r"\1 PM", regex=True)
    return s.str.upper()


def _parse_dt(text: pd.Series, order: str) -> pd.Series:
    t = text.astype(str).str.strip()
    t = t.str.replace(r"(\d{1,2}:\d{2}(:\d{2})?)\s*([ap])$", lambda m: f"{m.group(1)} {m.group(3).upper()}M", regex=True)
    out = pd.to_datetime(t, dayfirst=(order == "DMY"), yearfirst=(order == "YMD"), errors="coerce", format="mixed")
    return out


def convert(raw: RawFile, m: Mapping) -> tuple[pd.DataFrame, list[str]]:
    """Return canonical records (metric) and messages."""
    df = raw.data
    msgs: list[str] = []
    inv = {}
    for h, f in m.columns.items():
        if f != "skip" and h in df.columns:
            inv.setdefault(f, h)
    if "datetime" in inv:
        ts = _parse_dt(df[inv["datetime"]], m.date_order)
    elif "date" in inv and "time" in inv:
        ts = _parse_dt(df[inv["date"]].astype(str) + " " + _parse_time(df[inv["time"]]), m.date_order)
    elif "date" in inv:
        ts = _parse_dt(df[inv["date"]], m.date_order)
    else:
        raise ValueError("No date/time column mapped.")
    bad = ts.isna().sum()
    if bad:
        msgs.append(f"{bad} rows had dates that could not be read and were skipped.")
    out = pd.DataFrame({"ts_local": ts})
    for f in FIELDS:
        if f not in inv:
            continue
        col = df[inv[f]]
        if f in ("wind_dir_deg", "gust_dir_deg"):
            txt = col.astype(str).str.strip().str.upper()
            v = txt.map(COMPASS)
            v = v.fillna(_num(col))
        else:
            v = _num(col)
        kind = FIELD_UNIT_KIND.get(f)
        if kind == "temp":
            v = _to_c(v, m.units.get("temp", "C"))
        elif kind == "wind":
            v = _to_kmh(v, m.units.get("wind", "km/h"))
        elif kind == "rain":
            v = _to_mm(v, m.units.get("rain", "mm"))
        elif kind == "bar":
            v = _to_hpa(v, m.units.get("bar", "hPa"))
        out[f] = v.astype(float)
    if "interval" in inv:
        out["interval_min"] = _num(df[inv["interval"]])
    out = out.dropna(subset=["ts_local"]).sort_values("ts_local")
    if "interval_min" not in out or out["interval_min"].isna().all():
        step = out["ts_local"].diff().dt.total_seconds().div(60).median()
        out["interval_min"] = float(step) if pd.notna(step) else np.nan
    tz = ZoneInfo(m.timezone)
    loc = out["ts_local"].dt.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
    out["ts_utc"] = loc.dt.tz_convert("UTC").dt.strftime("%Y-%m-%d %H:%M:%S")
    out["ts_local"] = out["ts_local"].dt.strftime("%Y-%m-%d %H:%M:%S")
    dup = out.duplicated("ts_utc").sum()
    if dup:
        msgs.append(f"{dup} duplicate timestamps in the file were dropped.")
        out = out.drop_duplicates("ts_utc")
    return out.reset_index(drop=True), msgs
