"""Rainfall module: forecast (tutorial 8), SDII (10), anomaly (13), temporal clustering (15)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from core import ee_client
from core.area import Area
from core.datasets import get
from core.period import Period
from core.result import ChartSpec, LegendItem, MapLayer

from . import eeh
from .base import Analysis, Param, RunContext, register


def chirps(start, end):
    import ee

    return ee.ImageCollection(get("chirps").ee_id).filterDate(start, end).select("precipitation")



# ---------------------------------------------------------------------------
# Tutorial 8: precipitation forecast (GFS)
# ---------------------------------------------------------------------------

@register
class PrecipForecast(Analysis):
    id = "precip_forecast"
    tutorial_no = 8
    module = "Rainfall"
    default_title = "Precipitation forecast (GFS)"
    datasets = ["gfs"]
    params = [Param("scale", "Output scale (m)", 27830, "int", min=5000, max=50000)]

    def run(self, area: Area, period: Period, params: dict[str, Any] | None = None, ctx: RunContext | None = None):
        import ee

        ctx = ctx or RunContext()
        p = self.clean_params(params)
        ee_client.ensure()
        res = self.new_result(area, period, p)
        res.warnings = [w for w in res.warnings if "period" not in w.lower()]  # the forecast ignores the analysis period
        geom = area.to_ee()
        scale = p["scale"]

        ctx.step(0.1, "Finding the latest GFS forecast run")
        now = ee.Date(datetime.now(timezone.utc).isoformat())
        col = ee.ImageCollection(get("gfs").ee_id).filterDate(now.advance(-3, "day"), now.advance(1, "day"))
        latest = col.aggregate_max("creation_time")
        run = col.filter(ee.Filter.eq("creation_time", latest)).filter(ee.Filter.lte("forecast_hours", 168))
        run_ms = latest.getInfo()
        if run_ms is None:
            raise RuntimeError("No recent GFS forecast found in Earth Engine. Try again later.")
        run_time = datetime.fromtimestamp(run_ms / 1000, tz=timezone.utc)

        ctx.step(0.3, "Reading the forecast for each 6-hour step")
        steps = run.filter(ee.Filter.eq("forecast_hours", 0).Not()).sort("forecast_hours")

        def lab(img):
            return img.set("label", ee.Number(img.get("forecast_hours")).format("%d"))

        df = eeh.region_series(steps.map(lab), geom, ["total_precipitation_surface"], scale)
        df["hour"] = df["label"].astype(int)
        df = df.sort_values("hour").drop_duplicates("hour")
        v = df["total_precipitation_surface"].astype(float).fillna(0).to_numpy()
        # VERIFY on first real run: GFS precipitation in Earth Engine is treated as accumulated from the run
        # start. If the series is not non-decreasing, treat values as per-step amounts and add them up.
        accumulated = bool(np.all(np.diff(v) >= -0.05))
        df["cum_mm"] = v if accumulated else np.cumsum(v)
        df["step_mm"] = np.diff(np.concatenate([[0], df["cum_mm"].to_numpy()]))
        df["valid_time_utc"] = [(run_time + pd.Timedelta(hours=int(h))).strftime("%d %b %H:%M") for h in df["hour"]]
        local_offset = pd.Timedelta(hours=5, minutes=30)
        df["valid_time_IST"] = [(run_time + pd.Timedelta(hours=int(h)) + local_offset).strftime("%d %b %H:%M") for h in df["hour"]]

        ctx.step(0.6, "Building 24-, 72- and 168-hour rain maps")
        hours = sorted(set(df["hour"]))

        def acc_img(h: int):
            if accumulated:
                return ee.Image(run.filter(ee.Filter.eq("forecast_hours", h)).first()).select("total_precipitation_surface")
            return run.filter(ee.Filter.gt("forecast_hours", 0)).filter(ee.Filter.lte("forecast_hours", h)) \
                      .select("total_precipitation_surface").sum()

        layers, rows = [], []
        rain_vis = {"min": 0, "max": 150, "palette": eeh.RAIN_PALETTE}
        rain_leg = [LegendItem("0 mm", eeh.RAIN_PALETTE[0]), LegendItem("75 mm", eeh.RAIN_PALETTE[3]),
                    LegendItem("150+ mm", eeh.RAIN_PALETTE[-1])]
        for h in (24, 72, 168):
            hh = max([x for x in hours if x <= h], default=None)
            if hh is None:
                continue
            img = acc_img(hh).clip(geom).rename("rain")
            stats = eeh.region_value(img, geom, scale, "mean")
            mx = eeh.region_value(img, geom, scale, "max")
            rows.append({"Next": f"{h} hours", "Area-average rain (mm)": round(stats.get("rain") or 0, 1),
                         "Highest in area (mm)": round(mx.get("rain") or 0, 1)})
            layers.append(MapLayer(f"Rain, next {h} h", img, rain_vis, rain_leg, f"Forecast rain, next {h} h (mm)",
                                   continuous=True, shown=(h == 72)))

        res.map_layers = layers
        res.stats = pd.DataFrame(rows)
        res.stats_title = f"Forecast rain from the GFS run of {run_time:%d %b %Y %H:%M} UTC"
        daily = df.copy()
        daily["day"] = [(run_time + pd.Timedelta(hours=int(h)) + local_offset - pd.Timedelta(minutes=1)).strftime("%a %d %b") for h in daily["hour"]]
        per_day = daily.groupby("day", sort=False)["step_mm"].sum().round(1).reset_index().rename(columns={"step_mm": "Rain (mm)"})
        res.charts = [
            ChartSpec("Forecast rain per day (area average, IST days)", "bar", per_day, "day", ["Rain (mm)"], y_title="mm"),
            ChartSpec("Accumulated forecast rain (area average)", "line", df[["valid_time_IST", "cum_mm"]].rename(columns={"cum_mm": "Accumulated (mm)"}),
                      "valid_time_IST", ["Accumulated (mm)"], y_title="mm"),
        ]
        wettest = per_day.loc[per_day["Rain (mm)"].idxmax()] if not per_day.empty else None
        r72 = next((r for r in rows if r["Next"] == "72 hours"), None)
        res.key_numbers = {"run_utc": run_time.strftime("%Y-%m-%d %H:%M"), "rain_next_72h_mm": r72 and r72["Area-average rain (mm)"],
                           "max_next_72h_mm": r72 and r72["Highest in area (mm)"],
                           "wettest_day": None if wettest is None else wettest["day"],
                           "wettest_day_mm": None if wettest is None else wettest["Rain (mm)"]}
        if r72:
            res.summary = (f"The GFS forecast run of {run_time:%d %b %H:%M} UTC expects about {r72['Area-average rain (mm)']:.0f} mm "
                           f"of rain on average over {area.name} in the next 72 hours, up to {r72['Highest in area (mm)']:.0f} mm in "
                           f"the wettest part. " + (f"The wettest day looks like {wettest['day']} ({wettest['Rain (mm)']:.0f} mm)." if wettest is not None else ""))
        res.method = {"formula": "Accumulated total precipitation from the latest GFS 0.25° run up to each forecast hour",
                      "datasets": ["NOAA/GFS0P25 total_precipitation_surface"], "parameters": {"run": f"{run_time:%Y-%m-%d %H:%M} UTC"},
                      "scale": f"{scale} m (native about 28 km)", "device": "Earth Engine (cloud)"}
        res.limitations = ["A global model forecast at about 28 km; local thunderstorms and hill effects are not resolved.",
                           "This is not an official forecast or warning. Check India Meteorological Department bulletins for warnings."]
        res.citations = [get("gfs").citation]
        res.baseline_label = ""
        res.period_label = f"Forecast from {run_time:%d %b %Y %H:%M} UTC, next 7 days"
        ctx.step(1.0, "Done")
        return res


# ---------------------------------------------------------------------------
# Tutorial 10: precipitation intensity index (SDII)
# ---------------------------------------------------------------------------

def _yearly_sdii(col_all, years: list[int], months: tuple[int, ...], wet_mm: float):
    import ee

    def per_year(y):
        y = ee.Number(y)
        sub = col_all.filter(ee.Filter.calendarRange(y, y, "year"))
        if months:
            sub = sub.filter(ee.Filter.Or(*[ee.Filter.calendarRange(m, m, "month") for m in months]))
        wet = sub.map(lambda i: i.gte(wet_mm).rename("wet"))
        wet_days = wet.sum().rename("wet_days")
        wet_total = sub.map(lambda i: i.updateMask(i.gte(wet_mm))).sum().unmask(0).rename("wet_total")
        total = sub.sum().rename("total")
        sdii = wet_total.divide(wet_days.max(1)).updateMask(wet_days.gt(0)).rename("SDII")
        return (ee.Image.cat([sdii, wet_days, total]).set("year", y).set("label", y.format("%d"))
                .set("system:time_start", ee.Date.fromYMD(y, 1, 1).millis()))

    return ee.ImageCollection.fromImages(ee.List(years).map(per_year))


@register
class PrecipSDII(Analysis):
    id = "precip_sdii"
    tutorial_no = 10
    module = "Rainfall"
    default_title = "Precipitation intensity index (SDII)"
    datasets = ["chirps"]
    params = [
        Param("wet_mm", "Wet-day threshold (mm)", 1.0, "float", min=0.1, max=10,
              help="ETCCDI uses 1 mm; IMD 'rainy day' uses 2.5 mm"),
        Param("scale", "Output scale (m)", 5566, "int", min=5566, max=50000),
    ]

    def run(self, area: Area, period: Period, params: dict[str, Any] | None = None, ctx: RunContext | None = None):
        import ee

        ctx = ctx or RunContext()
        p = self.clean_params(params)
        ee_client.ensure()
        res = self.new_result(area, period, p)
        geom = area.to_ee()
        scale = max(p["scale"], area.suggested_scale(5566))
        years = period.years()
        base_years = list(range(period.baseline_start, period.baseline_end + 1))

        ctx.step(0.1, "Yearly SDII and wet days for the analysis years")
        col_all = chirps(eeh.ee_date(date(min(years[0], base_years[0]), 1, 1)), eeh.ee_date(period.end).advance(1, "day"))
        col_period = col_all.filterDate(eeh.ee_date(period.start), eeh.ee_date(period.end).advance(1, "day"))
        yearly = _yearly_sdii(col_period, years, period.months, p["wet_mm"])
        df = eeh.region_series(yearly, geom, ["SDII", "wet_days", "total"], scale)
        df = df.rename(columns={"label": "Year", "SDII": "SDII (mm/wet day)", "wet_days": "Wet days", "total": "Total rain (mm)"})
        df = df.astype({"SDII (mm/wet day)": float, "Wet days": float, "Total rain (mm)": float}).round(1)

        ctx.step(0.45, "Baseline SDII")
        base = _yearly_sdii(col_all, base_years, period.months, p["wet_mm"])
        base_mean = base.select("SDII").mean().rename("SDII_base")
        base_val = eeh.region_value(base_mean, geom, scale).get("SDII_base")

        ctx.step(0.7, "SDII and change maps")
        sdii_map = yearly.select("SDII").mean().clip(geom)
        change = sdii_map.subtract(base_mean).rename("change").clip(geom)
        res.map_layers = [
            MapLayer("SDII (mm per wet day)", sdii_map, {"min": 5, "max": 30, "palette": eeh.RAIN_PALETTE},
                     [LegendItem("5", eeh.RAIN_PALETTE[0]), LegendItem("17", eeh.RAIN_PALETTE[3]), LegendItem("30+", eeh.RAIN_PALETTE[-1])],
                     "SDII, period average (mm per wet day)", True),
            MapLayer("Change vs baseline", change, {"min": -5, "max": 5, "palette": eeh.ANOM_PALETTE},
                     [LegendItem("−5 (gentler)", eeh.ANOM_PALETTE[0]), LegendItem("0", eeh.ANOM_PALETTE[3]),
                      LegendItem("+5 (heavier bursts)", eeh.ANOM_PALETTE[-1])], f"SDII change vs {period.baseline_label} (mm per wet day)",
                     True, shown=False),
        ]
        x = df["Year"].astype(int).to_numpy()
        y = df["SDII (mm/wet day)"].to_numpy()
        ok = ~np.isnan(y)
        slope = float(np.polyfit(x[ok], y[ok], 1)[0]) if ok.sum() >= 3 else None
        res.stats = df
        res.stats_title = "SDII, wet days and rain per year" + (" (selected months only)" if period.months else "")
        res.charts = [
            ChartSpec("SDII per year (area average)", "bar", df, "Year", ["SDII (mm/wet day)"], y_title="mm per wet day",
                      extra={"hlines": [(base_val, f"Baseline {period.baseline_label}")] if base_val else [], "trend": True}),
            ChartSpec("Wet days per year", "bar", df, "Year", ["Wet days"], y_title="days"),
        ]
        mean_sdii = float(np.nanmean(y)) if ok.any() else None
        res.key_numbers = {"mean_sdii": mean_sdii and round(mean_sdii, 1), "baseline_sdii": base_val and round(base_val, 1),
                           "trend_mm_per_year": slope and round(slope, 2), "years": len(df)}
        if mean_sdii is not None:
            cmp = ""
            if base_val:
                d = mean_sdii - base_val
                cmp = (f" That is {abs(d):.1f} mm {'more' if d > 0 else 'less'} per rainy day than the {period.baseline_label} "
                       f"average ({base_val:.1f} mm), so rain is arriving in {'heavier' if d > 0 else 'gentler'} bursts.")
            tr = (f" Over these years the value {'rose' if slope > 0 else 'fell'} by about {abs(slope):.2f} mm per wet day per year "
                  "(a simple straight-line trend; Phase 3 adds a significance test).") if slope is not None else ""
            res.summary = f"On rainy days, {area.name} received on average {mean_sdii:.1f} mm.{cmp}{tr}"
        res.method = {"formula": f"SDII = total rain on wet days (≥ {p['wet_mm']:g} mm) ÷ number of wet days, per year"
                      + (" (selected months only)" if period.months else ""),
                      "datasets": ["UCSB-CHG/CHIRPS/DAILY"], "baseline": period.baseline_label, "parameters": {"wet-day threshold": f"{p['wet_mm']:g} mm"},
                      "scale": f"{scale} m", "device": "Earth Engine (cloud)"}
        res.limitations = ["CHIRPS blends satellite estimates with station data; daily amounts in hilly terrain are less certain."]
        res.citations = ["ETCCDI climate change indices (SDII).", get("chirps").citation]
        ctx.step(1.0, "Done")
        return res


# ---------------------------------------------------------------------------
# Tutorial 13: precipitation anomaly detection
# ---------------------------------------------------------------------------

PCT_CLASSES = [(50, "Very dry (< 50%)", "#8c510a"), (80, "Dry (50–80%)", "#d8b365"), (120, "Near normal (80–120%)", "#f5f5f5"),
               (150, "Wet (120–150%)", "#5ab4ac"), (1e9, "Very wet (> 150%)", "#01665e")]


@register
class PrecipAnomaly(Analysis):
    id = "precip_anomaly"
    tutorial_no = 13
    module = "Rainfall"
    default_title = "Precipitation anomaly detection"
    datasets = ["chirps"]
    needs_separate_baseline = True
    params = [Param("scale", "Output scale (m)", 5566, "int", min=5566, max=50000)]

    def run(self, area: Area, period: Period, params: dict[str, Any] | None = None, ctx: RunContext | None = None):
        import ee

        ctx = ctx or RunContext()
        p = self.clean_params(params)
        ee_client.ensure()
        res = self.new_result(area, period, p)
        geom = area.to_ee()
        scale = max(p["scale"], area.suggested_scale(5566))
        months = eeh.month_index_list(period.start, period.end, period.months)

        ctx.step(0.1, "Monthly rainfall totals and baseline normals")
        col_all = chirps(eeh.ee_date(date(min(period.baseline_start, period.start.year), 1, 1)), eeh.ee_date(period.end).advance(1, "day"))
        monthly = eeh.monthly_collection(col_all, "precipitation", months, reducer="sum")
        clim = eeh.monthly_climatology(col_all, "precipitation", period.baseline_start, period.baseline_end, reducer="sum")

        def add_normal(img):
            c = eeh.clim_for(clim, img.get("month"))
            normal = c.select("precipitation_mean").rename("normal")
            sd = c.select("precipitation_std").rename("sd")
            z = img.subtract(normal).divide(sd.max(0.1)).rename("z")
            return ee.Image.cat([img, normal, z]).copyProperties(img, ["system:time_start", "label", "month", "year"])

        with_n = monthly.map(add_normal)
        ctx.step(0.4, "Area totals per month")
        df = eeh.region_series(with_n, geom, ["precipitation", "normal", "z"], scale)
        df = df.rename(columns={"label": "Month", "precipitation": "Rain (mm)", "normal": "Normal (mm)", "z": "Standardised anomaly"})
        for c in ("Rain (mm)", "Normal (mm)", "Standardised anomaly"):
            df[c] = df[c].astype(float)
        df["% of normal"] = (100 * df["Rain (mm)"] / df["Normal (mm)"].replace(0, np.nan)).round(0)
        df = df.round(1)
        df["Flag"] = [next((lab for b, lab, _ in PCT_CLASSES if v < b), "") if pd.notna(v) else "No data" for v in df["% of normal"]]

        ctx.step(0.65, "Percent-of-normal map for the whole period")
        total = with_n.select("precipitation").sum()
        normal = with_n.select("normal").sum()
        pct = total.divide(normal.max(0.1)).multiply(100).rename("pct").clip(geom)
        cls = eeh.classify(pct, [b for b, _, _ in PCT_CLASSES])
        res.map_layers = [
            MapLayer("Rain as % of normal", cls, {"min": 0, "max": 4, "palette": [c for _, _, c in PCT_CLASSES]},
                     [LegendItem(lab, c) for _, lab, c in PCT_CLASSES], f"Rain as % of {period.baseline_label} normal"),
            MapLayer("% of normal (continuous)", pct, {"min": 25, "max": 175, "palette": eeh.ANOM_PALETTE},
                     [LegendItem("25%", eeh.ANOM_PALETTE[0]), LegendItem("100%", eeh.ANOM_PALETTE[3]), LegendItem("175%", eeh.ANOM_PALETTE[-1])],
                     "% of normal", True, shown=False),
        ]
        tot, nor = df["Rain (mm)"].sum(), df["Normal (mm)"].sum()
        pct_all = 100 * tot / nor if nor else None
        res.stats = df
        res.stats_title = "Monthly rain vs normal"
        res.charts = [
            ChartSpec("Monthly rain vs normal (area average)", "bar", df, "Month", ["Rain (mm)", "Normal (mm)"], y_title="mm",
                      extra={"overlay_second_as_line": True}),
            ChartSpec("Standardised anomaly by month", "bar", df, "Month", ["Standardised anomaly"], y_title="standard deviations",
                      extra={"signed": True, "hlines": [(1, "Very wet"), (-1, "Very dry")]}),
        ]
        wettest = df.loc[df["Standardised anomaly"].idxmax()] if df["Standardised anomaly"].notna().any() else None
        driest = df.loc[df["Standardised anomaly"].idxmin()] if df["Standardised anomaly"].notna().any() else None
        res.key_numbers = {"total_mm": round(tot, 0), "normal_mm": round(nor, 0), "pct_of_normal": pct_all and round(pct_all, 0),
                           "wettest_month": None if wettest is None else wettest["Month"],
                           "driest_month": None if driest is None else driest["Month"]}
        if pct_all is not None:
            res.summary = (f"{area.name} received {tot:.0f} mm against a normal of {nor:.0f} mm for these months: {pct_all:.0f}% of normal. "
                           + (f"The most unusually wet month was {wettest['Month']} ({wettest['% of normal']:.0f}% of normal) and the "
                              f"most unusually dry was {driest['Month']} ({driest['% of normal']:.0f}%)." if wettest is not None else ""))
        res.method = {"formula": "% of normal = rain ÷ baseline mean for the same calendar month × 100; standardised anomaly = (rain − mean) ÷ standard deviation",
                      "datasets": ["UCSB-CHG/CHIRPS/DAILY (monthly totals)"], "baseline": period.baseline_label,
                      "scale": f"{scale} m", "device": "Earth Engine (cloud)"}
        res.limitations = ["Percent of normal is misleading in months that are normally almost dry; use the standardised anomaly there."]
        res.citations = [get("chirps").citation]
        ctx.step(1.0, "Done")
        return res


# ---------------------------------------------------------------------------
# Tutorial 15: precipitation temporal clustering
# ---------------------------------------------------------------------------

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@register
class PrecipCluster(Analysis):
    id = "precip_cluster"
    tutorial_no = 15
    module = "Rainfall"
    default_title = "Precipitation temporal clustering"
    datasets = ["chirps"]
    params = [
        Param("k", "Number of clusters", 4, "int", min=2, max=10),
        Param("mode", "Group", "places", "choice", ["places", "years"],
              help="places = map zones with similar monthly rainfall; years = years with similar patterns for this area"),
        Param("scale", "Output scale (m)", 5566, "int", min=5566, max=50000),
    ]

    def run(self, area: Area, period: Period, params: dict[str, Any] | None = None, ctx: RunContext | None = None):
        ctx = ctx or RunContext()
        p = self.clean_params(params)
        ee_client.ensure()
        res = self.new_result(area, period, p)
        if p["mode"] == "years":
            return self._years(area, period, p, res, ctx)
        return self._places(area, period, p, res, ctx)

    def _places(self, area, period, p, res, ctx):
        import ee

        geom = area.to_ee()
        scale = max(p["scale"], area.suggested_scale(5566))
        k = int(p["k"])
        mons = list(period.months) or list(range(1, 13))
        ctx.step(0.1, "Monthly rainfall climatology per pixel")
        col = chirps(eeh.ee_date(period.start), eeh.ee_date(period.end).advance(1, "day"))
        n_years = max(1, (period.end - period.start).days / 365.25)
        bands = []
        for m in mons:
            bands.append(col.filter(ee.Filter.calendarRange(m, m, "month")).sum().divide(n_years).rename(MONTH_NAMES[m - 1]))
        stack = ee.Image.cat(bands).clip(geom)
        ctx.step(0.3, "Training k-means on sample pixels")
        sample = stack.sample(region=geom, scale=scale, numPixels=5000, seed=42, geometries=False)
        clusterer = ee.Clusterer.wekaKMeans(k, seed=42).train(sample)
        cl = stack.cluster(clusterer).rename("cluster")
        ctx.step(0.55, "Cluster rainfall profiles")
        names = [MONTH_NAMES[m - 1] for m in mons]
        prof = stack.addBands(cl).reduceRegion(
            ee.Reducer.mean().repeat(len(names)).group(groupField=len(names), groupName="cluster"),
            geom, scale, bestEffort=True, maxPixels=eeh.MAX_PIXELS, tileScale=4).getInfo()
        groups = prof.get("groups", [])
        rows = []
        for g in groups:
            vals = g["mean"]
            rows.append({"cluster": int(g["cluster"]), **{n: round(v or 0, 1) for n, v in zip(names, vals)}})
        prof_df = pd.DataFrame(rows)
        # Relabel clusters 1..k by total rainfall (driest = 1) so labels are stable between runs.
        prof_df["Total (mm/yr)"] = prof_df[names].sum(axis=1).round(0)
        prof_df = prof_df.sort_values("Total (mm/yr)").reset_index(drop=True)
        prof_df["Zone"] = [f"Zone {i + 1}" for i in range(len(prof_df))]
        prof_df["Wettest month"] = prof_df[names].idxmax(axis=1)
        old = prof_df["cluster"].tolist()
        new = list(range(len(old)))
        cl_sorted = cl.remap(old, new).rename("zone")
        ctx.step(0.75, "Area of each zone")
        areas = eeh.class_areas(cl_sorted, geom, scale, {i: f"Zone {i + 1}" for i in new})
        prof_df = prof_df.merge(areas.rename(columns={"Class": "Zone"}), on="Zone", how="left")
        cols = eeh.CLUSTER_COLOURS[:len(new)]
        res.map_layers = [MapLayer("Rainfall zones", cl_sorted, {"min": 0, "max": max(1, len(new) - 1), "palette": cols},
                                   [LegendItem(f"Zone {i + 1} ({int(t)} mm/yr, peak {w})", c) for i, (t, w, c) in
                                    enumerate(zip(prof_df["Total (mm/yr)"], prof_df["Wettest month"], cols))],
                                   "Rainfall zones (driest = Zone 1)")]
        long = prof_df.melt(id_vars=["Zone"], value_vars=names, var_name="Month", value_name="Rain (mm)")
        wide = long.pivot(index="Month", columns="Zone", values="Rain (mm)").reindex(names).reset_index()
        res.stats = prof_df[["Zone", "Total (mm/yr)", "Wettest month", "Area (km²)", "Share (%)"]]
        res.stats_title = "Rainfall zones"
        res.charts = [ChartSpec("Average monthly rain in each zone", "line", wide, "Month", [z for z in prof_df["Zone"]],
                                y_title="mm per month", colors=cols)]
        big = prof_df.loc[prof_df["Share (%)"].idxmax()] if not prof_df.empty else None
        res.key_numbers = {"zones": len(prof_df), "largest_zone": None if big is None else big["Zone"],
                           "largest_zone_share_pct": None if big is None else big["Share (%)"]}
        res.summary = (f"{area.name} splits into {len(prof_df)} rainfall zones. "
                       + " ".join(f"{r['Zone']} gets about {r['Total (mm/yr)']:.0f} mm a year, peaking in {r['Wettest month']} "
                                  f"({r['Share (%)']:.0f}% of the area)." for _, r in prof_df.iterrows()))
        res.method = {"formula": "k-means clustering (Earth Engine wekaKMeans) of per-pixel average monthly rainfall; zones numbered from driest to wettest",
                      "datasets": ["UCSB-CHG/CHIRPS/DAILY"], "parameters": {"clusters": k, "sample pixels": 5000, "months": ", ".join(names)},
                      "scale": f"{scale} m", "device": "Earth Engine (cloud)"}
        res.limitations = ["Zones are statistical groups, not official climate zones; small changes in k or period can move boundaries."]
        res.citations = [get("chirps").citation]
        ctx.step(1.0, "Done")
        return res

    def _years(self, area, period, p, res, ctx):
        from sklearn.cluster import KMeans

        from .drought import chirps_monthly_area_totals

        ctx.step(0.1, "Monthly rainfall for each year")
        df = chirps_monthly_area_totals(area, period, include_baseline=False)
        df = df[(df["date"] >= pd.Timestamp(period.start.replace(day=1))) & (df["date"] <= pd.Timestamp(period.end))]
        df["year"], df["m"] = df["date"].dt.year, df["date"].dt.month
        mons = list(period.months) or list(range(1, 13))
        piv = df.pivot_table(index="year", columns="m", values="rain_mm").reindex(columns=mons).dropna()
        k = min(int(p["k"]), len(piv))
        if k < 2:
            raise ValueError("Need at least two complete years to group years. Choose a longer period.")
        ctx.step(0.5, "Grouping years with k-means (on this PC)")
        X = piv.to_numpy()
        km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(X)
        piv["group"] = km.labels_
        order = piv.groupby("group")[mons].sum().sum(axis=1).sort_values().index.tolist()
        piv["Group"] = piv["group"].map({g: f"Group {i + 1}" for i, g in enumerate(order)})
        names = [MONTH_NAMES[m - 1] for m in mons]
        prof = piv.groupby("Group")[mons].mean().round(1)
        prof.columns = names
        wide = prof.T.reset_index().rename(columns={"index": "Month"})
        table = piv.reset_index()[["year", "Group"]].rename(columns={"year": "Year"})
        table["Total (mm)"] = piv[mons].sum(axis=1).round(0).to_numpy()
        res.stats = table
        res.stats_title = "Year groups"
        res.charts = [ChartSpec("Average monthly rain in each group of years", "line", wide, "Month", list(prof.index), y_title="mm per month")]
        res.summary = f"The {len(piv)} complete years for {area.name} fall into {k} groups with similar monthly rainfall: " + "; ".join(
            f"{g}: {', '.join(str(y) for y in table[table['Group'] == g]['Year'])}" for g in prof.index) + "."
        res.key_numbers = {"groups": k, "years": len(piv)}
        res.method = {"formula": "k-means (scikit-learn) on each year's area-average monthly rainfall; groups numbered from driest to wettest",
                      "datasets": ["UCSB-CHG/CHIRPS/DAILY"], "parameters": {"groups": k}, "device": "Earth Engine (cloud) + this PC (CPU)"}
        res.device_used = "Earth Engine (cloud) + CPU"
        res.citations = [get("chirps").citation]
        ctx.step(1.0, "Done")
        return res
