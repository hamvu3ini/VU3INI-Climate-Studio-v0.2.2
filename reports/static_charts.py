"""ChartSpec -> PNG with Matplotlib, for reports (works offline, no browser needed)."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from core.result import ChartSpec  # noqa: E402

from .charts import PALETTE, _name  # noqa: E402

plt.rcParams.update({
    "font.family": ["DejaVu Sans"], "font.size": 9, "axes.titlesize": 11, "axes.titleweight": "bold",
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": "#e9ecef",
    "grid.linewidth": 0.8, "axes.axisbelow": True, "legend.frameon": False, "figure.dpi": 100,
})


def _short_dates(labels: list[str]) -> list[str]:
    """2026-04-01 -> 01 Apr (adds the year when the span crosses years)."""
    import re
    from datetime import datetime

    if labels and all(re.match(r"^\d{4}-\d{2}$", s) for s in labels):
        return [datetime.strptime(s, "%Y-%m").strftime("%b %Y") for s in labels]
    if not labels or not all(re.match(r"^\d{4}-\d{2}-\d{2}$", s) for s in labels):
        return labels
    years = {s[:4] for s in labels}
    fmt = "%d %b" if len(years) == 1 else "%d %b %y"
    return [datetime.strptime(s, "%Y-%m-%d").strftime(fmt) for s in labels]


def _ticks(ax, labels: list[str]):
    labels = _short_dates(labels)
    n = len(labels)
    step = max(1, int(np.ceil(n / 12)))
    idx = list(range(0, n, step))
    ax.set_xticks(idx)
    ax.set_xticklabels([labels[i] for i in idx], rotation=35 if n > 6 else 0, ha="right" if n > 6 else "center")


def to_png(spec: ChartSpec, path: str, width_in: float = 9.0, height_in: float = 3.8) -> str:
    d = spec.data.reset_index(drop=True)
    ex = spec.extra or {}
    colors = spec.colors or PALETTE
    labels = d[spec.x].astype(str).tolist() if spec.x in d else [str(i) for i in d.index]
    xs = np.arange(len(d))

    if spec.kind == "windrose":
        fig = plt.figure(figsize=(6, 6))
        ax = fig.add_subplot(111, projection="polar")
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        theta = np.deg2rad(np.arange(16) * 22.5)
        bottom = np.zeros(16)
        rose_cols = ["#c6dbef", "#6baed6", "#2171b5", "#08519c", "#08306b"]
        for i, c in enumerate(spec.y):
            v = d[c].to_numpy(dtype=float)
            ax.bar(theta, v, width=np.deg2rad(20), bottom=bottom, color=rose_cols[i % 5], edgecolor="white", linewidth=0.5,
                   label=f"{c} km/h")
            bottom += v
        ax.set_xticks(theta)
        ax.set_xticklabels(d[spec.x].tolist(), fontsize=8)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
        ax.set_rlabel_position(202.5)
        ax.tick_params(axis="y", labelsize=7, labelcolor="#495057")
        ax.set_title(spec.title, pad=18)
        ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), title="Speed", fontsize=8)
        if "calm_pct" in ex:
            fig.text(0.02, 0.02, f"Calm: {ex['calm_pct']}% of the time", fontsize=8)
        fig.savefig(path, dpi=200, bbox_inches="tight")
        plt.close(fig)
        return path

    fig, ax = plt.subplots(figsize=(width_in, height_in))
    if spec.kind == "band" and len(spec.y) == 3:
        hi, mid, lo = (d[c].to_numpy(dtype=float) for c in spec.y)
        lab = ex.get("labels", [_name(c) for c in spec.y])
        ax.fill_between(xs, lo, hi, color="#1f6fb4", alpha=0.15, label=f"{lab[2]}–{lab[0]} range", linewidth=0)
        ax.plot(xs, hi, color="#c92a2a", lw=1, label=lab[0])
        ax.plot(xs, mid, color="#1f6fb4", lw=1.8, label=lab[1])
        ax.plot(xs, lo, color="#1864ab", lw=1, ls=":", label=lab[2])
    elif spec.kind == "bar":
        if ex.get("color_by_class") and len(spec.y) == 1:
            v = d[spec.y[0]].to_numpy(dtype=float)
            b, cols = ex["bounds"], ex["class_colours"]
            mc = [next((cols[i] for i, bb in enumerate(b) if val < bb), cols[-1]) if not np.isnan(val) else "#ced4da" for val in v]
            ax.bar(xs, np.nan_to_num(v), color=mc, width=0.8)
        elif ex.get("signed") and len(spec.y) == 1:
            v = d[spec.y[0]].to_numpy(dtype=float)
            ax.bar(xs, np.nan_to_num(v), color=["#01665e" if val >= 0 else "#8c510a" for val in np.nan_to_num(v)], width=0.8)
            ax.axhline(0, color="#495057", lw=0.8)
        elif ex.get("overlay_second_as_line") and len(spec.y) == 2:
            ax.bar(xs, d[spec.y[0]].astype(float).fillna(0), color=colors[0], width=0.75, label=_name(spec.y[0]))
            ax.plot(xs, d[spec.y[1]].astype(float), color="#212529", ls="--", marker="o", ms=3, lw=1.5, label=_name(spec.y[1]))
        else:
            n = len(spec.y)
            w = 0.8 / n
            for i, c in enumerate(spec.y):
                ax.bar(xs + (i - (n - 1) / 2) * w, d[c].astype(float).fillna(0), width=w, color=colors[i % len(colors)], label=_name(c))
        if ex.get("trend") and len(spec.y) == 1:
            yv = d[spec.y[0]].to_numpy(dtype=float)
            ok = ~np.isnan(yv)
            if ok.sum() >= 3:
                k, b0 = np.polyfit(xs[ok], yv[ok], 1)
                ax.plot(xs, k * xs + b0, color="#c92a2a", ls="--", lw=1.2, label="Trend")
    else:
        sec = ex.get("secondary_y")
        ax2 = ax.twinx() if sec else None
        for i, c in enumerate(spec.y):
            target = ax2 if (sec and c == sec) else ax
            v = d[c].to_numpy(dtype=float)
            if spec.kind == "area":
                target.fill_between(xs, 0, v, color=colors[i % len(colors)], alpha=0.25)
            target.plot(xs, v, color=colors[i % len(colors)], lw=1.8, label=_name(c),
                        marker="o" if len(d) <= 40 else None, ms=3)
        if ax2 is not None:
            ax2.set_ylabel(_name(sec))
            ax2.grid(False)
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=4, fontsize=8)
    for val, lab in ex.get("hlines", []):
        if val is None:
            continue
        ax.axhline(val, color="#868e96", ls=":", lw=1)
        ax.annotate(lab, xy=(0, val), xycoords=("axes fraction", "data"), xytext=(2, 2), textcoords="offset points",
                    fontsize=7, color="#495057")
    ax.set_title(spec.title, loc="left")
    ax.set_ylabel(spec.y_title)
    if spec.x_title:
        ax.set_xlabel(spec.x_title)
    _ticks(ax, labels)
    ax.set_xlim(-0.6, len(d) - 0.4)
    if ax.get_legend() is None and len(ax.get_legend_handles_labels()[0]) > 1:
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=min(5, len(ax.get_legend_handles_labels()[0])), fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path
