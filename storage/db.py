"""SQLite storage: favourite areas, saved runs, station records and mappings."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from core import paths

_lock = threading.RLock()

STATION_VARS = [
    "temp_c", "temp_hi_c", "temp_lo_c", "hum_pct", "dew_c", "wind_kmh", "wind_dir_deg",
    "gust_kmh", "gust_dir_deg", "wind_chill_c", "heat_index_c", "bar_hpa", "rain_mm",
    "rain_rate_mmh", "solar_wm2", "solar_hi_wm2", "uv_index", "uv_hi", "et_mm",
    "soil_cb_1", "soil_cb_2", "soil_cb_3", "soil_cb_4", "soil_temp_c_1", "soil_temp_c_2",
    "leaf_wet_1", "in_temp_c", "in_hum_pct",
]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS areas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    data TEXT NOT NULL,
    created TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis TEXT NOT NULL,
    area_name TEXT,
    period TEXT,
    params TEXT,
    result_dir TEXT,
    created TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS station_mappings (
    station TEXT NOT NULL,
    signature TEXT NOT NULL,
    mapping TEXT NOT NULL,
    PRIMARY KEY (station, signature)
);
CREATE TABLE IF NOT EXISTS station_records (
    station TEXT NOT NULL,
    ts_local TEXT NOT NULL,
    ts_utc TEXT NOT NULL,
    interval_min REAL,
    excluded INTEGER DEFAULT 0,
    {", ".join(f"{v} REAL" for v in STATION_VARS)},
    PRIMARY KEY (station, ts_utc)
);
CREATE INDEX IF NOT EXISTS ix_station_local ON station_records(station, ts_local);
CREATE TABLE IF NOT EXISTS geocode_cache (
    query TEXT PRIMARY KEY,
    result TEXT NOT NULL
);
"""


def _path() -> Path:
    return paths.database_file()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    with _lock:
        con = sqlite3.connect(_path())
        try:
            con.executescript(SCHEMA)
            yield con
            con.commit()
        finally:
            con.close()


# ---- favourite areas ----------------------------------------------------------

def save_area(name: str, data: dict[str, Any]) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO areas(name, data) VALUES (?, ?)", (name, json.dumps(data)))


def list_areas() -> list[tuple[str, dict[str, Any]]]:
    with connect() as con:
        return [(n, json.loads(d)) for n, d in con.execute("SELECT name, data FROM areas ORDER BY name")]


def delete_area(name: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM areas WHERE name = ?", (name,))


# ---- runs -------------------------------------------------------------------------

def add_run(analysis: str, area_name: str, period: dict, params: dict, result_dir: str) -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO runs(analysis, area_name, period, params, result_dir) VALUES (?, ?, ?, ?, ?)",
            (analysis, area_name, json.dumps(period, default=str), json.dumps(params, default=str), result_dir),
        )
        return int(cur.lastrowid)


def list_runs(limit: int = 100) -> pd.DataFrame:
    with connect() as con:
        return pd.read_sql_query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", con, params=(limit,))


# ---- station ---------------------------------------------------------------------

def save_mapping(station: str, signature: str, mapping: dict[str, Any]) -> None:
    with connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO station_mappings(station, signature, mapping) VALUES (?, ?, ?)",
            (station, signature, json.dumps(mapping)),
        )


def get_mapping(station: str, signature: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute(
            "SELECT mapping FROM station_mappings WHERE station = ? AND signature = ?", (station, signature)
        ).fetchone()
    return json.loads(row[0]) if row else None


def upsert_station_records(station: str, df: pd.DataFrame) -> tuple[int, int]:
    """Insert records; existing timestamps are left untouched (no duplicates).

    Returns (inserted, skipped).
    """
    cols = ["ts_local", "ts_utc", "interval_min"] + [v for v in STATION_VARS if v in df.columns]
    rows = df[cols].copy()
    rows["ts_local"] = rows["ts_local"].astype(str)
    rows["ts_utc"] = rows["ts_utc"].astype(str)
    rows = rows.astype(object).where(pd.notna(rows), None)
    sql = (
        f"INSERT OR IGNORE INTO station_records(station, {', '.join(cols)}) "
        f"VALUES (?, {', '.join('?' for _ in cols)})"
    )
    with connect() as con:
        before = con.execute("SELECT COUNT(*) FROM station_records WHERE station = ?", (station,)).fetchone()[0]
        con.executemany(sql, [(station, *r) for r in rows.itertuples(index=False, name=None)])
        after = con.execute("SELECT COUNT(*) FROM station_records WHERE station = ?", (station,)).fetchone()[0]
    inserted = after - before
    return inserted, len(rows) - inserted


def load_station(station: str, start: str | None = None, end: str | None = None, include_excluded: bool = False) -> pd.DataFrame:
    q = "SELECT * FROM station_records WHERE station = ?"
    params: list[Any] = [station]
    if start:
        q += " AND ts_local >= ?"
        params.append(start)
    if end:
        q += " AND ts_local <= ?"
        params.append(end + " 23:59:59" if len(end) == 10 else end)
    if not include_excluded:
        q += " AND excluded = 0"
    q += " ORDER BY ts_local"
    with connect() as con:
        df = pd.read_sql_query(q, con, params=params)
    if not df.empty:
        df["ts_local"] = pd.to_datetime(df["ts_local"])
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    return df


def set_excluded(station: str, ts_utc_list: list[str], excluded: bool = True) -> None:
    with connect() as con:
        con.executemany(
            "UPDATE station_records SET excluded = ? WHERE station = ? AND ts_utc = ?",
            [(1 if excluded else 0, station, ts) for ts in ts_utc_list],
        )


def station_range(station: str) -> tuple[str | None, str | None, int]:
    with connect() as con:
        r = con.execute(
            "SELECT MIN(ts_local), MAX(ts_local), COUNT(*) FROM station_records WHERE station = ?", (station,)
        ).fetchone()
    return r[0], r[1], r[2]


# ---- geocode cache ----------------------------------------------------------------

def geocode_get(query: str) -> Any | None:
    with connect() as con:
        r = con.execute("SELECT result FROM geocode_cache WHERE query = ?", (query.lower(),)).fetchone()
    return json.loads(r[0]) if r else None


def geocode_put(query: str, result: Any) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO geocode_cache(query, result) VALUES (?, ?)", (query.lower(), json.dumps(result)))
