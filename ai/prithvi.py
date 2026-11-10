"""Prithvi (NASA-IBM) geospatial AI models: flood mapping and land cover.

Pipeline:
  1. Read the model card config from Hugging Face (bands, normalisation, tile size).
  2. Export those bands for the area and date window from Earth Engine as GeoTIFF tiles
     (download limits mean large areas are fetched in chunks), and mosaic them.
  3. Normalise, tile with overlap, run inference on the device chosen by compute/device.py
     (GPU when worth it, CPU otherwise; CUDA out-of-memory -> smaller batch -> CPU).
  4. Stitch, save a GeoTIFF and a coloured PNG, and return an AnalysisResult.

VERIFY on the user's PC before relying on results:
  * the model repo names and their licences on Hugging Face;
  * the TerraTorch loading call (`LightningInferenceModel`) for the installed TerraTorch version;
  * the band order and reflectance scaling the fine-tune expects (read from its config).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from compute import device as device_mod
from core import ee_client, paths
from core.area import Area
from core.result import AnalysisResult, LegendItem, MapLayer
from i18n import t

log = logging.getLogger(__name__)


@dataclass
class ModelSpec:
    key: str
    repo: str
    title: str
    classes: dict[int, tuple[str, str]]         # value -> (label, colour)
    ee_collection: str
    ee_bands: list[str]                          # in the order the model expects
    scale_m: int = 30
    reflectance_divisor: float = 1.0             # set from config if the model expects 0-1 reflectance
    tile: int = 512
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


MODELS: dict[str, ModelSpec] = {
    "prithvi_flood": ModelSpec(
        key="prithvi_flood", repo="ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11",
        title="Flood map (Prithvi AI)", classes={0: ("Not water", "#f1f3f5"), 1: ("Water / flood", "#1c7ed6")},
        ee_collection="COPERNICUS/S2_HARMONIZED", ee_bands=["B2", "B3", "B4", "B8A", "B11", "B12"],
        scale_m=20, tile=512,
        notes="Sen1Floods11 fine-tune: Sentinel-2 blue, green, red, narrow NIR, SWIR1, SWIR2. Cloud cover hides water.",
    ),
    "prithvi_landcover": ModelSpec(
        key="prithvi_landcover", repo="ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-VERIFY-landcover",
        title="Land cover (Prithvi AI)", classes={},
        ee_collection="COPERNICUS/S2_HARMONIZED", ee_bands=["B2", "B3", "B4", "B8A", "B11", "B12"], scale_m=30, tile=224,
        notes="VERIFY: choose a published Prithvi land-cover or crop fine-tune on Hugging Face and fill in its classes.",
    ),
}


def available() -> tuple[bool, str]:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False, "PyTorch is not installed. Run setup again to install the AI components."
    try:
        import terratorch  # noqa: F401
    except ImportError:
        return False, "TerraTorch is not installed. Run setup again (it installs TerraTorch for the AI features)."
    return True, ""


# ---------------------------------------------------------------------------
# model loading
# ---------------------------------------------------------------------------

_loaded: dict[str, Any] = {}


def _download(spec: ModelSpec, progress=None) -> tuple[Path, Path]:
    from huggingface_hub import hf_hub_download, list_repo_files

    cache = paths.models_dir()
    files = list_repo_files(spec.repo)
    cfg = next((f for f in files if f.endswith((".yaml", ".yml")) and "config" in f.lower()), None) or \
        next((f for f in files if f.endswith((".yaml", ".yml"))), None)
    ckpt = next((f for f in files if f.endswith((".ckpt", ".pt", ".pth", ".safetensors"))), None)
    if not cfg or not ckpt:
        raise RuntimeError(f"Could not find a config and checkpoint in {spec.repo}. Check the model name in ai/prithvi.py.")
    if progress:
        progress(0.05, f"Downloading model files from Hugging Face ({spec.repo}); this happens once")
    c = Path(hf_hub_download(spec.repo, cfg, cache_dir=cache))
    k = Path(hf_hub_download(spec.repo, ckpt, cache_dir=cache))
    return c, k


def read_config(cfg_path: Path) -> dict[str, Any]:
    import yaml

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data = (cfg.get("data") or {}).get("init_args", {})
    return {"means": data.get("means"), "stds": data.get("stds"), "bands": data.get("bands") or data.get("output_bands"),
            "img_size": (cfg.get("model", {}).get("init_args", {}).get("model_args", {}) or {}).get("img_size")}


def load_model(spec: ModelSpec, dev: device_mod.Device, progress=None):
    key = f"{spec.key}:{dev.torch_device}"
    if key in _loaded:
        return _loaded[key]
    import torch
    from terratorch.cli_tools import LightningInferenceModel  # VERIFY for the installed TerraTorch version

    cfg_path, ckpt_path = _download(spec, progress)
    lm = LightningInferenceModel.from_config(str(cfg_path), str(ckpt_path))
    model = lm.model
    model.eval()
    model.to(torch.device(dev.torch_device))
    info = read_config(cfg_path)
    _loaded[key] = (model, info)
    return model, info


# ---------------------------------------------------------------------------
# input imagery from Earth Engine
# ---------------------------------------------------------------------------

def s2_composite(area: Area, start: date, end: date, bands: list[str], max_cloud: int = 60):
    import ee

    col = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED").filterBounds(area.to_ee()).filterDate(start.isoformat(), (end + timedelta(days=1)).isoformat())
           .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", max_cloud)))

    def mask(img):
        qa = img.select("QA60")
        clear = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
        return img.updateMask(clear)

    n = col.size().getInfo()
    if n == 0:
        raise RuntimeError("No Sentinel-2 images with acceptable cloud cover in that window. Widen the dates or use the radar flood map.")
    return col.map(mask).median().select(bands).toUint16(), n


def download_bands(img, area: Area, scale_m: int, out: Path, progress=None) -> Path:
    """Fetch a multiband GeoTIFF in chunks that respect Earth Engine's download limit, then mosaic."""
    import rasterio
    import requests
    from rasterio.merge import merge

    w, s, e, n = area.bounds
    lat = (s + n) / 2
    px_x = (e - w) * 111_320 * math.cos(math.radians(lat)) / scale_m
    px_y = (n - s) * 110_540 / scale_m
    nb = img.bandNames().size().getInfo()
    max_px = int(math.sqrt(30e6 / (nb * 2)))  # stay well under ~32 MB per request
    nx, ny = max(1, math.ceil(px_x / max_px)), max(1, math.ceil(px_y / max_px))
    parts = []
    tmp = out.parent / (out.stem + "_parts")
    tmp.mkdir(parents=True, exist_ok=True)
    import ee

    k = 0
    for i in range(nx):
        for j in range(ny):
            k += 1
            if progress:
                progress(0.1 + 0.3 * k / (nx * ny), f"Downloading imagery tile {k} of {nx * ny}")
            box = [w + (e - w) * i / nx, s + (n - s) * j / ny, w + (e - w) * (i + 1) / nx, s + (n - s) * (j + 1) / ny]
            url = img.getDownloadURL({"region": ee.Geometry.Rectangle(box), "scale": scale_m, "crs": "EPSG:4326",
                                      "format": "GEO_TIFF", "filePerBand": False})
            p = tmp / f"part_{i}_{j}.tif"
            r = requests.get(url, timeout=600)
            r.raise_for_status()
            p.write_bytes(r.content)
            parts.append(p)
    srcs = [rasterio.open(p) for p in parts]
    mosaic, transform = merge(srcs)
    meta = srcs[0].meta.copy()
    for s_ in srcs:
        s_.close()
    meta.update(height=mosaic.shape[1], width=mosaic.shape[2], transform=transform)
    with rasterio.open(out, "w", **meta) as dst:
        dst.write(mosaic)
    return out


# ---------------------------------------------------------------------------
# tiled inference
# ---------------------------------------------------------------------------

def normalise(arr: np.ndarray, means, stds, divisor: float = 1.0) -> np.ndarray:
    x = arr.astype(np.float32) / divisor
    if means and stds:
        m = np.asarray(means, dtype=np.float32)[:, None, None]
        sd = np.asarray(stds, dtype=np.float32)[:, None, None]
        x = (x - m) / sd
    return np.nan_to_num(x)


def tiles(h: int, w: int, size: int, overlap: int) -> list[tuple[int, int]]:
    step = size - overlap
    ys = list(range(0, max(1, h - overlap), step)) or [0]
    xs = list(range(0, max(1, w - overlap), step)) or [0]
    return [(y, x) for y in ys for x in xs]


def predict(model, x: np.ndarray, dev: device_mod.Device, size: int, overlap: int = 64, progress=None) -> np.ndarray:
    """x: (bands, H, W) normalised. Returns class index per pixel (H, W)."""
    import torch

    bands, H, W = x.shape
    pad_h, pad_w = max(0, size - H), max(0, size - W)
    xp = np.pad(x, ((0, 0), (0, pad_h + size), (0, pad_w + size)), mode="reflect")
    out = np.zeros((xp.shape[1], xp.shape[2]), dtype=np.uint8)
    coords = tiles(H + pad_h, W + pad_w, size, overlap)
    half = overlap // 2
    batch = max(1, dev.batch_size)
    tdev = torch.device(dev.torch_device)
    use_amp = dev.kind == "cuda"
    for b0 in range(0, len(coords), batch):
        chunk = coords[b0:b0 + batch]
        arr = np.stack([xp[:, y:y + size, x_:x_ + size] for y, x_ in chunk])
        with torch.no_grad(), torch.autocast(device_type="cuda" if use_amp else "cpu", dtype=torch.float16 if use_amp else torch.bfloat16, enabled=use_amp):
            t = torch.from_numpy(arr).to(tdev)
            o = model(t)
            logits = getattr(o, "output", o)
            pred = logits.argmax(1).to("cpu").numpy().astype(np.uint8)
        for (y, x_), p in zip(chunk, pred):
            y0, x0 = (half if y > 0 else 0), (half if x_ > 0 else 0)
            out[y + y0:y + size, x_ + x0:x_ + size] = p[y0:, x0:]
        if progress:
            progress(0.45 + 0.45 * (b0 + len(chunk)) / len(coords), f"AI model: tile {b0 + len(chunk)} of {len(coords)} on {dev.label()}")
    return out[:H, :W]


# ---------------------------------------------------------------------------
# public entry
# ---------------------------------------------------------------------------

def run(model_key: str, area: Area, start: date, end: date, progress=None) -> AnalysisResult:
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    import rasterio

    spec = MODELS[model_key]
    if not spec.classes:
        raise RuntimeError(spec.notes)
    ee_client.ensure()
    mgr = device_mod.manager()
    work = paths.cache_dir() / f"{model_key}_{start:%Y%m%d}_{end:%Y%m%d}"
    work.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(0.05, "Preparing satellite imagery")
    img, n_images = s2_composite(area, start, end, spec.ee_bands)
    tif = download_bands(img, area, spec.scale_m, work / "input.tif", progress)
    with rasterio.open(tif) as src:
        arr = src.read()
        profile = src.profile
        bounds = src.bounds
    n_tiles = len(tiles(arr.shape[1], arr.shape[2], spec.tile, 64))

    def job(dev: device_mod.Device):
        model, info = load_model(spec, dev, progress)
        x = normalise(arr, info.get("means"), info.get("stds"), spec.reflectance_divisor)
        return predict(model, x, dev, spec.tile, progress=progress)

    pred, dev = mgr.run_with_fallback("prithvi", job, size=n_tiles)
    valid = (arr > 0).any(axis=0)
    pred = np.where(valid, pred, 255).astype(np.uint8)
    out_tif = work / "prediction.tif"
    profile.update(count=1, dtype="uint8", nodata=255)
    with rasterio.open(out_tif, "w", **profile) as dst:
        dst.write(pred, 1)

    # coloured PNG overlay
    from PIL import Image

    rgba = np.zeros((*pred.shape, 4), dtype=np.uint8)
    for v, (_, col) in spec.classes.items():
        c = tuple(int(col[i:i + 2], 16) for i in (1, 3, 5))
        rgba[pred == v] = (*c, 200 if v else 60)
    png = work / "prediction.png"
    Image.fromarray(rgba, "RGBA").save(png)

    px_km2 = (spec.scale_m / 1000) ** 2
    rows = []
    total = int(valid.sum())
    import pandas as pd

    for v, (lab, _) in spec.classes.items():
        npx = int((pred == v).sum())
        rows.append({"Class": lab, "Area (km²)": round(npx * px_km2, 1), "Share (%)": round(100 * npx / max(1, total), 1)})
    res = AnalysisResult(
        analysis_id=model_key, title=f"{spec.title}: {area.name}", area_name=area.name, area_source=area.source,
        period_label=f"{start:%d %b %Y} – {end:%d %b %Y}", info_what=t(f"info.{model_key}.what"),
        info_example=t(f"info.{model_key}.example"), area_geometry=area.geometry,
        device_used=f"{dev.label()} on this PC ({dev.reason})",
    )
    res.map_layers = [MapLayer(spec.title, None, {}, [LegendItem(l, c) for l, c in spec.classes.values()], spec.title,
                               png_path=str(png), bounds=(bounds.left, bounds.bottom, bounds.right, bounds.top))]
    res.stats = pd.DataFrame(rows)
    res.stats_title = "Area by class"
    water = next((r for r in rows if "water" in r["Class"].lower()), None)
    res.summary = (f"The Prithvi AI model mapped {water['Area (km²)']:.1f} km² of water ({water['Share (%)']:.1f}% of the clear-sky area) in "
                   f"{area.name} from a cloud-masked Sentinel-2 composite of {n_images} image(s)." if water else
                   f"The Prithvi AI model classified {area.name} into {len(rows)} classes.")
    res.key_numbers = {r["Class"]: r["Area (km²)"] for r in rows} | {"images": n_images, "device": dev.label()}
    res.method = {"formula": f"Deep-learning segmentation with {spec.repo}", "datasets": [f"{spec.ee_collection} bands {spec.ee_bands}, median of {n_images} image(s), QA60 cloud mask"],
                  "parameters": {"tile": spec.tile, "scale": f"{spec.scale_m} m"}, "device": res.device_used}
    res.limitations = ["AI outputs are estimates and need checking; compare with the Sentinel-1 radar flood map (Phase 2).",
                       "Clouds hide the ground in optical images, so flooded areas under cloud are missed.", spec.notes]
    res.citations = [f"Model: {spec.repo} (Hugging Face).", "Szwarcman, D. et al. (2024). Prithvi-EO-2.0: A Versatile Multi-Temporal Foundation Model for Earth Observation Applications."]
    return res
