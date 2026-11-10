"""ChartSpec -> Plotly figure (interactive in the app, PNG in reports)."""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.result import ChartSpec

PALETTE = ["#1f6fb4", "#e8590c", "#2f9e44", "#ae3ec9", "#f59f00", "#0c8599", "#c2255c", "#495057"]
FONT = "Noto Sans, DejaVu Sans, Segoe UI, Arial, sans-serif"

PRETTY = {
    "tmax_c": "High", "tmean_c": "Average", "tmin_c": "Low", "rain_mm": "Rain", "chirps_rain_mm": "CHIRPS rain",
    "era5_tmean_c": "ERA5-Land", "wind_mean_kmh": "Average wind", "gust_max_kmh": "Strongest gust",
    "rh_max": "Max", "rh_mean": "Average", "rh_min": "Min", "dew_mean_c": "Average dew point", "dew_max_c": "Highest dew point",
    "heat_index_max_c": "Highest heat index", "bar_hpa": "Pressure", "bar_drop_12h_hpa": "12-hour drop", "solar_mj": "Solar energy",
    "uv_max": "Peak UV", "soil_1_cb": "Soil sensor 1", "soil_2_cb": "Soil sensor 2", "soil_3_cb": "Soil sensor 3",
    "soil_4_cb": "Soil sensor 4", "et_mm": "Evapotranspiration", "cum": "Accumulated rain", "smap_sm_m3m3": "SMAP soil moisture",
}


def _name(c: str) -> str:
    return PRETTY.get(c, c)


def figure(spec: ChartSpec, dark: bool = False, height: int = 380) -> go.Figure:
    d = spec.data
    x = d[spec.x].astype(str) if spec.x in d else d.index.astype(str)
    colors = spec.colors or PALETTE
    ex = spec.extra or {}
    sec = ex.get("secondary_y")
    fig = make_subplots(specs=[[{"secondary_y": bool(sec)}]])

    if spec.kind == "windrose":
        fig = go.Figure()
        dirs = d[spec.x].tolist()
        rose_cols = ["#c6dbef", "#6baed6", "#2171b5", "#08519c", "#08306b"]
        for i, c in enumerate(spec.y):
            fig.add_trace(go.Barpolar(r=d[c], theta=dirs, name=f"{c} km/h", marker_color=rose_cols[i % len(rose_cols)]))
        fig.update_layout(polar=dict(angularaxis=dict(direction="clockwise", rotation=90), radialaxis=dict(ticksuffix="%")),
                          legend_title_text="Speed")
        if "calm_pct" in ex:
            fig.add_annotation(text=f"Calm {ex['calm_pct']}%", x=0, y=0, xref="paper", yref="paper", showarrow=False)
    elif spec.kind == "band" and len(spec.y) == 3:
        hi, mid, lo = spec.y
        labels = ex.get("labels", [_name(c) for c in spec.y])
        fig.add_trace(go.Scatter(x=x, y=d[hi], mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=x, y=d[lo], mode="lines", line=dict(width=0), fill="tonexty",
                                 fillcolor="rgba(31,111,180,0.18)", name=f"{labels[2]}–{labels[0]} range"))
        fig.add_trace(go.Scatter(x=x, y=d[hi], mode="lines", line=dict(color="#c92a2a", width=1.2), name=labels[0]))
        fig.add_trace(go.Scatter(x=x, y=d[mid], mode="lines", line=dict(color="#1f6fb4", width=2), name=labels[1]))
        fig.add_trace(go.Scatter(x=x, y=d[lo], mode="lines", line=dict(color="#1864ab", width=1.2, dash="dot"), name=labels[2]))
    elif spec.kind == "bar":
        if ex.get("color_by_class") and len(spec.y) == 1:
            vals = d[spec.y[0]].astype(float)
            bounds, cols = ex["bounds"], ex["class_colours"]
            mc = [next((cols[i] for i, b in enumerate(bounds) if v < b), cols[-1]) if not np.isnan(v) else "#ced4da" for v in vals]
            fig.add_trace(go.Bar(x=x, y=vals, marker_color=mc, name=_name(spec.y[0])))
        elif ex.get("signed") and len(spec.y) == 1:
            vals = d[spec.y[0]].astype(float)
            fig.add_trace(go.Bar(x=x, y=vals, marker_color=["#01665e" if v >= 0 else "#8c510a" for v in vals.fillna(0)],
                                 name=_name(spec.y[0])))
        elif ex.get("overlay_second_as_line") and len(spec.y) == 2:
            fig.add_trace(go.Bar(x=x, y=d[spec.y[0]], name=_name(spec.y[0]), marker_color=colors[0]))
            fig.add_trace(go.Scatter(x=x, y=d[spec.y[1]], name=_name(spec.y[1]), mode="lines+markers",
                                     line=dict(color="#212529", width=2, dash="dash")))
        else:
            for i, c in enumerate(spec.y):
                fig.add_trace(go.Bar(x=x, y=d[c], name=_name(c), marker_color=colors[i % len(colors)]))
            fig.update_layout(barmode="group")
        if ex.get("trend") and len(spec.y) == 1:
            xv = np.arange(len(d))
            yv = d[spec.y[0]].astype(float).to_numpy()
            ok = ~np.isnan(yv)
            if ok.sum() >= 3:
                k, b = np.polyfit(xv[ok], yv[ok], 1)
                fig.add_trace(go.Scatter(x=x, y=k * xv + b, mode="lines", name="Trend", line=dict(color="#c92a2a", dash="dash")))
    else:  # line / area / scatter
        for i, c in enumerate(spec.y):
            on_sec = bool(sec) and c == sec
            mode = "markers" if spec.kind == "scatter" else "lines" if len(d) > 40 else "lines+markers"
            fig.add_trace(go.Scatter(x=x, y=d[c], name=_name(c), mode=mode, line=dict(color=colors[i % len(colors)], width=2),
                                     fill="tozeroy" if spec.kind == "area" else None), secondary_y=on_sec if sec else None)
    for val, lab in ex.get("hlines", []):
        if val is None:
            continue
        fig.add_hline(y=val, line_dash="dot", line_color="#868e96", annotation_text=lab, annotation_position="top left",
                      annotation_font_size=10)
    fig.update_layout(
        title=dict(text=spec.title, font=dict(size=15)), height=height, margin=dict(l=60, r=20, t=55, b=50),
        font=dict(family=FONT, size=12, color="#e9ecef" if dark else "#212529"),
        template="plotly_dark" if dark else "plotly_white", legend=dict(orientation="h", y=-0.18),
        hovermode="x unified" if spec.kind not in ("windrose",) else "closest",
    )
    if spec.kind != "windrose":
        fig.update_yaxes(title_text=spec.y_title, secondary_y=False)
        if spec.x_title:
            fig.update_xaxes(title_text=spec.x_title)
        if len(d) > 60:
            fig.update_xaxes(nticks=12)
    if spec.note:
        fig.add_annotation(text=spec.note, xref="paper", yref="paper", x=0, y=1.07, showarrow=False, font=dict(size=10))
    return fig


def to_png(spec: ChartSpec, path: str, width: int = 1000, height: int = 420) -> str:
    fig = figure(spec, height=height)
    fig.write_image(path, width=width, height=height, scale=2)
    return path
