"""The common result object every analysis and station report returns.

It holds everything the UI and the report engine need: map layers, a statistics
table, chart data, a plain-language summary, a method note and the info card.
`save()` writes a self-contained copy (PNG maps, CSV tables, JSON) so results
open offline later.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import paths

log = logging.getLogger(__name__)


@dataclass
class LegendItem:
    label: str
    color: str  # hex


@dataclass
class MapLayer:
    name: str
    ee_image: Any = None              # ee.Image (not saved)
    vis: dict[str, Any] = field(default_factory=dict)
    legend: list[LegendItem] = field(default_factory=list)
    legend_title: str = ""
    continuous: bool = False          # colour bar instead of classes
    png_path: str = ""                # static rendering for reports / offline
    bounds: tuple[float, float, float, float] | None = None
    tile_url: str = ""                # filled when displayed (expires)
    shown: bool = True


@dataclass
class ChartSpec:
    title: str
    kind: str                         # line | bar | area | scatter | windrose | heatmap | band
    data: pd.DataFrame
    x: str
    y: list[str]
    y_title: str = ""
    x_title: str = ""
    colors: list[str] = field(default_factory=list)
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. threshold lines, bands
    png_path: str = ""


@dataclass
class AnalysisResult:
    analysis_id: str
    title: str
    area_name: str
    area_source: str
    period_label: str
    baseline_label: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    map_layers: list[MapLayer] = field(default_factory=list)
    stats: pd.DataFrame | None = None
    stats_title: str = "Statistics"
    charts: list[ChartSpec] = field(default_factory=list)
    summary: str = ""
    key_numbers: dict[str, Any] = field(default_factory=dict)   # used by the AI explain panel
    method: dict[str, Any] = field(default_factory=dict)       # formula, datasets, parameters, scale, device
    info_what: str = ""
    info_example: str = ""
    tutorial_url: str = ""
    warnings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    device_used: str = "Earth Engine (cloud)"
    ai_explanation: str = ""
    created: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    saved_dir: str = ""
    area_geometry: dict[str, Any] | None = None

    def method_text(self) -> str:
        parts = []
        for k in ("formula", "steps", "datasets", "baseline", "parameters", "scale", "device"):
            v = self.method.get(k)
            if not v:
                continue
            if isinstance(v, (list, tuple)):
                v = "; ".join(str(x) for x in v)
            elif isinstance(v, dict):
                v = ", ".join(f"{kk} = {vv}" for kk, vv in v.items())
            parts.append(f"{k.capitalize()}: {v}")
        return "\n".join(parts)

    # -- persistence --------------------------------------------------------------
    def save(self, base: Path | None = None) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{self.analysis_id}_{self.area_name}")[:60]
        d = (base or paths.results_dir()) / f"{stamp}_{slug}"
        d.mkdir(parents=True, exist_ok=True)
        meta = {k: v for k, v in self.__dict__.items() if k not in ("map_layers", "stats", "charts")}
        meta["map_layers"] = [
            {k: v for k, v in m.__dict__.items() if k not in ("ee_image", "tile_url")} | {"legend": [l.__dict__ for l in m.legend]}
            for m in self.map_layers
        ]
        if self.stats is not None:
            self.stats.to_csv(d / "statistics.csv", index=False)
        meta["charts"] = []
        for i, c in enumerate(self.charts):
            f = d / f"chart_{i}.csv"
            c.data.to_csv(f, index=False)
            meta["charts"].append({k: v for k, v in c.__dict__.items() if k != "data"} | {"data_file": f.name})
        (d / "result.json").write_text(json.dumps(meta, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        self.saved_dir = str(d)
        return d

    @classmethod
    def load(cls, d: str | Path) -> "AnalysisResult":
        d = Path(d)
        meta = json.loads((d / "result.json").read_text(encoding="utf-8"))
        layers = []
        for m in meta.pop("map_layers", []):
            legend = [LegendItem(**x) for x in m.pop("legend", [])]
            if m.get("bounds"):
                m["bounds"] = tuple(m["bounds"])
            layers.append(MapLayer(**m, legend=legend))
        charts = []
        for c in meta.pop("charts", []):
            data = pd.read_csv(d / c.pop("data_file"))
            charts.append(ChartSpec(**c, data=data))
        stats = pd.read_csv(d / "statistics.csv") if (d / "statistics.csv").exists() else None
        known = {k: v for k, v in meta.items() if k in cls.__dataclass_fields__}
        return cls(**known, map_layers=layers, charts=charts, stats=stats)
