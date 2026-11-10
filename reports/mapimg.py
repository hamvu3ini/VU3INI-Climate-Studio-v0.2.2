"""Static map images for reports: Earth Engine thumbnail + outline, then legend, scale bar and north arrow.

Needs Earth Engine for analyses; station reports have no maps. Saved PNGs are
reused when a result is reopened offline.
"""
from __future__ import annotations

import io
import logging
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import requests  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from PIL import Image  # noqa: E402

from core.result import MapLayer  # noqa: E402

log = logging.getLogger(__name__)


def padded_bounds(bounds: tuple[float, float, float, float], pad: float = 0.06) -> tuple[float, float, float, float]:
    w, s, e, n = bounds
    dx, dy = (e - w) * pad or 0.05, (n - s) * pad or 0.05
    return w - dx, s - dy, e + dx, n + dy


def _thumb(ee_img, bbox, max_px: int = 1400) -> Image.Image:
    import ee

    region = ee.Geometry.Rectangle(list(bbox), proj="EPSG:4326", geodesic=False)
    url = ee_img.getThumbURL({"region": region, "dimensions": max_px, "format": "png"})
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGBA")


def ee_composite(layer: MapLayer, geometry: dict, with_hillshade: bool = True):
    """Hillshade background + the layer (semi-transparent) + area outline, as one RGB ee.Image."""
    import ee

    parts = []
    if with_hillshade:
        hs = ee.Terrain.hillshade(ee.Image("USGS/SRTMGL1_003")).visualize(min=120, max=255, palette=["c8c8c8", "ffffff"])
        parts.append(hs)
    parts.append(layer.ee_image.visualize(**layer.vis).updateMask(layer.ee_image.mask().reduce(ee.Reducer.min()).multiply(0.9)))
    outline = ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(ee.Geometry(geometry))]), 1, 2).visualize(palette=["111111"])
    parts.append(outline)
    return ee.ImageCollection(parts).mosaic()


def _km_per_deg_lon(lat: float) -> float:
    return 111.320 * math.cos(math.radians(lat))


def _nice(x: float) -> float:
    p = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 5, 10):
        if m * p >= x:
            return m * p
    return 10 * p


def decorate(img: Image.Image, bbox, layer: MapLayer, title: str, path: str, source_note: str = "") -> str:
    w, s, e, n = bbox
    aspect = img.height / max(1, img.width)
    fig_w = 8.0
    fig, ax = plt.subplots(figsize=(fig_w, max(3.5, fig_w * aspect) + 0.6))
    ax.imshow(img, extent=(w, e, s, n), interpolation="bilinear")
    ax.set_xlim(w, e)
    ax.set_ylim(s, n)
    ax.set_aspect(1 / math.cos(math.radians((s + n) / 2)))
    ax.tick_params(labelsize=7)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{abs(v):.2f}°{'E' if v >= 0 else 'W'}"))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{abs(v):.2f}°{'N' if v >= 0 else 'S'}"))
    ax.set_title(title, loc="left", fontsize=11, fontweight="bold")
    # scale bar (bottom left)
    lat_c = (s + n) / 2
    span_km = (e - w) * _km_per_deg_lon(lat_c)
    bar_km = _nice(span_km / 5)
    bar_deg = bar_km / _km_per_deg_lon(lat_c)
    x0, y0 = w + (e - w) * 0.04, s + (n - s) * 0.05
    ax.add_patch(mpatches.Rectangle((x0, y0), bar_deg, (n - s) * 0.008, color="black"))
    ax.text(x0 + bar_deg / 2, y0 + (n - s) * 0.02, f"{bar_km:g} km", ha="center", va="bottom", fontsize=8,
            bbox=dict(facecolor="white", alpha=0.7, edgecolor="none", pad=1))
    # north arrow (top right)
    ax.annotate("N", xy=(0.95, 0.95), xytext=(0.95, 0.84), xycoords="axes fraction", ha="center", va="center", fontsize=10,
                fontweight="bold", arrowprops=dict(facecolor="black", width=3, headwidth=9, headlength=8))
    # legend
    if layer.continuous and layer.legend:
        cmap = LinearSegmentedColormap.from_list("l", [x.color for x in layer.legend] if len(layer.legend) > 1 else
                                                 layer.vis.get("palette", ["#ffffff", "#000000"]))
        pal = layer.vis.get("palette")
        if pal:
            cmap = LinearSegmentedColormap.from_list("l", [c if c.startswith("#") else f"#{c}" for c in pal])
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(layer.vis.get("min", 0), layer.vis.get("max", 1)))
        cb = fig.colorbar(sm, ax=ax, orientation="horizontal", fraction=0.04, pad=0.08)
        cb.set_label(layer.legend_title or layer.name, fontsize=8)
        cb.ax.tick_params(labelsize=7)
    elif layer.legend:
        handles = [mpatches.Patch(facecolor=x.color, edgecolor="#555", linewidth=0.4, label=x.label) for x in layer.legend]
        ax.legend(handles=handles, title=layer.legend_title or layer.name, loc="upper center", bbox_to_anchor=(0.5, -0.07),
                  ncol=min(3, len(handles)), fontsize=7.5, title_fontsize=8, frameon=False)
    if source_note:
        fig.text(0.01, 0.005, source_note, fontsize=6.5, color="#555")
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def render(layer: MapLayer, geometry: dict, bounds, path: str, title: str = "", source_note: str = "") -> str:
    bbox = padded_bounds(bounds)
    img = _thumb(ee_composite(layer, geometry), bbox)
    return decorate(img, bbox, layer, title or layer.name, path, source_note)


def render_locator(geometry: dict, bounds, path: str) -> str:
    """Small overview: the area outline in red over a wider hillshade."""
    import ee

    w, s, e, n = bounds
    cx, cy = (w + e) / 2, (s + n) / 2
    half = max(e - w, n - s) * 3 + 0.5
    bbox = (cx - half, cy - half, cx + half, cy + half)
    hs = ee.Terrain.hillshade(ee.Image("USGS/SRTMGL1_003")).visualize(min=120, max=255, palette=["bdbdbd", "ffffff"])
    water = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").gt(50).selfMask().visualize(palette=["9ecae1"])
    fc = ee.FeatureCollection([ee.Feature(ee.Geometry(geometry))])
    fill = ee.Image().byte().paint(fc, 1).visualize(palette=["e03131"], opacity=0.35)
    line = ee.Image().byte().paint(fc, 1, 2).visualize(palette=["c92a2a"])
    img = _thumb(ee.ImageCollection([hs, water, fill, line]).mosaic(), bbox, 700)
    fig, ax = plt.subplots(figsize=(3.6, 3.6))
    ax.imshow(img, extent=(bbox[0], bbox[2], bbox[1], bbox[3]))
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("Location", fontsize=9, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def render_outline_only(geometry: dict, path: str, title: str = "Study area") -> str:
    """Offline fallback: just the outline (no Earth Engine)."""
    from shapely.geometry import shape

    g = shape(geometry)
    fig, ax = plt.subplots(figsize=(4, 4))
    polys = [g] if g.geom_type == "Polygon" else list(getattr(g, "geoms", []))
    for p in polys:
        if p.geom_type != "Polygon":
            continue
        x, y = p.exterior.xy
        ax.fill(x, y, color="#ffe3e3", edgecolor="#c92a2a", linewidth=1.2)
    ax.set_aspect(1 / math.cos(math.radians(g.centroid.y)))
    ax.set_title(title, fontsize=9, loc="left")
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path
