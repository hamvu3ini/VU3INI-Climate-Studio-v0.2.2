"""Earth Engine helpers shared by analyses.

Heavy computing stays server-side; only area statistics and series come back.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
from dateutil.relativedelta import relativedelta

MAX_PIXELS = 1e13

# Palettes (colour-blind-aware where possible)
DROUGHT_CLASSES = [  # (upper bound exclusive, label, colour)
    (10, "Extreme drought", "#7f0000"),
    (20, "Severe drought", "#d7301f"),
    (30, "Moderate drought", "#fc8d59"),
    (40, "Mild drought", "#fdcc8a"),
    (101, "No drought", "#1a9850"),
]
VCI_PALETTE = ["#7f0000", "#d7301f", "#fc8d59", "#fdcc8a", "#d9ef8b", "#91cf60", "#1a9850"]
RAIN_PALETTE = ["#ffffcc", "#c7e9b4", "#7fcdbb", "#41b6c4", "#1d91c0", "#225ea8", "#0c2c84"]
ANOM_PALETTE = ["#8c510a", "#d8b365", "#f6e8c3", "#f5f5f5", "#c7eae5", "#5ab4ac", "#01665e"]
CLUSTER_COLOURS = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666", "#1f78b4", "#b2df8a"]


def ee_date(d: date):
    import ee

    return ee.Date(d.isoformat())


def month_index_list(start: date, end: date, months: tuple[int, ...] = ()) -> list[date]:
    out, d = [], start.replace(day=1)
    while d <= end:
        if not months or d.month in months:
            out.append(d)
        d += relativedelta(months=1)
    return out


def safe_reduce(col, reducer: str, band: str):
    """Reduce a possibly empty collection to a single-band image (masked if empty)."""
    import ee

    red = getattr(col, reducer)()
    empty = ee.Image.constant(0).rename(band).updateMask(ee.Image.constant(0))
    return ee.Image(ee.Algorithms.If(col.size().gt(0), red.select([0]).rename(band), empty))


def monthly_collection(col, band: str, month_starts: list[date], reducer: str = "mean"):
    """One image per month start (server-side), with 'month', 'year' and 'label' properties."""
    import ee

    starts = ee.List([d.isoformat() for d in month_starts])

    def per_month(s):
        s = ee.Date(s)
        sub = col.filterDate(s, s.advance(1, "month"))
        img = safe_reduce(sub, reducer, band)
        return (img.set("system:time_start", s.millis())
                   .set("month", s.get("month")).set("year", s.get("year"))
                   .set("label", s.format("YYYY-MM")).set("n", sub.size()))

    return ee.ImageCollection.fromImages(starts.map(per_month))


def monthly_climatology(col, band: str, y0: int, y1: int, reducer: str = "mean"):
    """For each calendar month: min, max, mean and stdDev across baseline years of the monthly composite.

    Returns an ImageCollection of 12 images with bands <band>_min, _max, _mean, _std and property 'month'.
    """
    import ee

    years = ee.List.sequence(y0, y1)

    def per_cal_month(m):
        m = ee.Number(m)

        def per_year(y):
            y = ee.Number(y)
            sub = col.filter(ee.Filter.calendarRange(y, y, "year")).filter(ee.Filter.calendarRange(m, m, "month"))
            return safe_reduce(sub, reducer, band)

        yearly = ee.ImageCollection.fromImages(years.map(per_year))
        stats = yearly.reduce(
            ee.Reducer.min().combine(ee.Reducer.max(), sharedInputs=True)
            .combine(ee.Reducer.mean(), sharedInputs=True).combine(ee.Reducer.stdDev(), sharedInputs=True)
        ).rename([f"{band}_min", f"{band}_max", f"{band}_mean", f"{band}_std"])
        return stats.set("month", m)

    return ee.ImageCollection.fromImages(ee.List.sequence(1, 12).map(per_cal_month))


def clim_for(clim, month):
    import ee

    return ee.Image(clim.filter(ee.Filter.eq("month", month)).first())


def region_series(col, geom, bands: list[str], scale: float, label_prop: str = "label",
                  reducer: str = "mean") -> pd.DataFrame:
    """Area-mean (or other reducer) of each image -> DataFrame with a 'label' column."""
    import ee

    red = {"mean": ee.Reducer.mean(), "sum": ee.Reducer.sum(), "max": ee.Reducer.max(), "min": ee.Reducer.min()}[reducer]

    def f(img):
        img = ee.Image(img)
        vals = img.select(bands).reduceRegion(red, geom, scale, bestEffort=True, maxPixels=MAX_PIXELS, tileScale=4)
        return ee.Feature(None, vals).set(label_prop, img.get(label_prop))

    fc = ee.FeatureCollection(col.map(f))
    info = fc.getInfo()
    rows = [feat["properties"] for feat in info["features"]]
    df = pd.DataFrame(rows)
    for b in bands:
        if b not in df.columns:
            df[b] = float("nan")
    return df[[label_prop] + bands]


def region_value(img, geom, scale: float, reducer: str = "mean") -> dict[str, Any]:
    import ee

    red = {"mean": ee.Reducer.mean(), "max": ee.Reducer.max(), "min": ee.Reducer.min(), "sum": ee.Reducer.sum()}[reducer]
    return img.reduceRegion(red, geom, scale, bestEffort=True, maxPixels=MAX_PIXELS, tileScale=4).getInfo()


def class_areas(class_img, geom, scale: float, labels: dict[int, str]) -> pd.DataFrame:
    """Area in km² and % per integer class value."""
    import ee

    res = (ee.Image.pixelArea().divide(1e6).addBands(class_img.rename("cls").toInt())
           .reduceRegion(ee.Reducer.sum().group(groupField=1, groupName="cls"), geom, scale,
                         bestEffort=True, maxPixels=MAX_PIXELS, tileScale=4).getInfo())
    groups = res.get("groups", [])
    total = sum(g["sum"] for g in groups) or 1.0
    rows = []
    for k, lab in labels.items():
        km2 = next((g["sum"] for g in groups if int(g["cls"]) == k), 0.0)
        rows.append({"Class": lab, "Area (km²)": round(km2, 1), "Share (%)": round(100 * km2 / total, 1)})
    return pd.DataFrame(rows)


def classify(img, bounds: list[float]):
    """Class index 0..len(bounds)-1 for value ranges [-inf,b0), [b0,b1), ..."""
    import ee

    out = ee.Image.constant(len(bounds) - 1)
    for i in reversed(range(len(bounds) - 1)):
        out = out.where(img.lt(bounds[i]), i)
    return out.updateMask(img.mask()).toInt()


def scale_for(area, native_m: int) -> int:
    return area.suggested_scale(native_m)
