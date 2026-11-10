"""Study area: five ways to choose one, one result.

An Area always carries a GeoJSON geometry in EPSG:4326, a name and the source
of the boundary (stated in every report).
"""
from __future__ import annotations

import json
import logging
import math
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests
from pyproj import Geod, Transformer
from shapely.geometry import Point, mapping, shape

log = logging.getLogger(__name__)
_geod = Geod(ellps="WGS84")

LARGE_AREA_KM2 = 500_000
MAX_VERTICES = 20_000  # above this, simplify before sending to Earth Engine
USER_AGENT = "VU3INI-Climate-Studio/0.1 (personal use; hamvu3ini@gmail.com)"


@dataclass
class Area:
    name: str
    geometry: dict[str, Any]           # GeoJSON geometry, EPSG:4326
    source: str                        # e.g. "FAO GAUL 2015", "Uploaded: assam_districts.shp", "Drawn on map"
    method: str                        # list | search | draw | upload | coords
    meta: dict[str, Any] = field(default_factory=dict)
    simplified_m: float = 0.0          # simplification tolerance applied, metres
    ee_asset: dict[str, Any] | None = None  # for list picks: {"id":..., "filters": {...}}

    # -- facts ----------------------------------------------------------------
    @property
    def shape(self):
        return shape(self.geometry)

    @property
    def area_km2(self) -> float:
        return geodesic_area_km2(self.shape)

    @property
    def centroid(self) -> tuple[float, float]:
        c = self.shape.representative_point() if not self.shape.is_valid else self.shape.centroid
        return c.y, c.x  # lat, lon

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.shape.bounds

    def warnings(self) -> list[str]:
        w = []
        if self.area_km2 > LARGE_AREA_KM2:
            w.append(f"This area is very large ({self.area_km2:,.0f} km²). Processing may be slow or hit Earth "
                     "Engine limits; consider a coarser scale.")
        if self.simplified_m:
            w.append(f"The boundary was simplified by about {self.simplified_m:.0f} m to stay within Earth Engine limits.")
        return w

    def summary(self) -> str:
        lat, lon = self.centroid
        return f"{self.name}: {self.area_km2:,.0f} km², centre {lat:.3f}, {lon:.3f} ({self.source})"

    def suggested_scale(self, native_m: int) -> int:
        """Coarser scale for big areas so requests finish."""
        km2 = self.area_km2
        if km2 > 2_000_000:
            return max(native_m, 5000)
        if km2 > LARGE_AREA_KM2:
            return max(native_m, 2000)
        if km2 > 50_000:
            return max(native_m, 1000)
        return native_m

    # -- Earth Engine ---------------------------------------------------------
    def to_ee(self):
        import ee

        if self.ee_asset:
            fc = ee.FeatureCollection(self.ee_asset["id"])
            for k, v in self.ee_asset.get("filters", {}).items():
                fc = fc.filter(ee.Filter.eq(k, v))
            return fc.geometry()
        return ee.Geometry(self.geometry, None, False)  # (geo_json, opt_proj, opt_geodesic)

    # -- persistence ----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Area":
        return cls(**d)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def geodesic_area_km2(geom) -> float:
    a, _ = _geod.geometry_area_perimeter(geom)
    return abs(a) / 1e6


def _n_vertices(geom) -> int:
    gj = mapping(geom)

    def count(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            return 1
        return sum(count(x) for x in c)

    return count(gj["coordinates"]) if "coordinates" in gj else sum(_n_vertices(shape(g)) for g in gj.get("geometries", []))


def simplify_for_ee(geom) -> tuple[Any, float]:
    """Simplify very detailed polygons; returns (geometry, tolerance_m)."""
    if _n_vertices(geom) <= MAX_VERTICES:
        return geom, 0.0
    lat = geom.centroid.y
    for tol_m in (25, 50, 100, 200, 500, 1000, 2000):
        tol_deg = tol_m / (111_320 * max(0.2, math.cos(math.radians(lat))))
        g = geom.simplify(tol_deg, preserve_topology=True)
        if _n_vertices(g) <= MAX_VERTICES:
            return g, float(tol_m)
    return geom.convex_hull, 9999.0


# ---------------------------------------------------------------------------
# 1. pick from list (Earth Engine boundary datasets)
# ---------------------------------------------------------------------------

def admin_names(level: int, parents: dict[str, str] | None = None, source: str = "FAO GAUL 2015") -> list[str]:
    """Distinct names at a level, optionally filtered by parent names. Needs Earth Engine."""
    import ee

    from .datasets import BOUNDARY_SOURCES

    src = BOUNDARY_SOURCES[source]
    asset, field_name = src[level]
    fc = ee.FeatureCollection(asset)
    for k, v in (parents or {}).items():
        fc = fc.filter(ee.Filter.eq(k, v))
    return sorted(set(fc.aggregate_array(field_name).getInfo()))


def from_admin(country: str, state: str | None = None, district: str | None = None,
               source: str = "FAO GAUL 2015") -> Area:
    import ee

    from .datasets import BOUNDARY_SOURCES

    src = BOUNDARY_SOURCES[source]
    level = 2 if district else 1 if state else 0
    asset, field_name = src[level]
    filters = {"ADM0_NAME": country}
    if state:
        filters["ADM1_NAME"] = state
    if district:
        filters["ADM2_NAME"] = district
    fc = ee.FeatureCollection(asset)
    for k, v in filters.items():
        fc = fc.filter(ee.Filter.eq(k, v))
    # Bring a simplified outline to the PC for display and area figures.
    geom = fc.geometry().simplify(maxError=100 if level == 2 else 500).getInfo()
    name = ", ".join(x for x in (district, state, country) if x)
    return Area(name=name, geometry=geom, source=source, method="list",
                ee_asset={"id": asset, "filters": filters}, meta={"level": level})


# ---------------------------------------------------------------------------
# 2. search a place (Nominatim, OpenStreetMap)
# ---------------------------------------------------------------------------

def geocode(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Look up a place name. Results are cached locally (Nominatim usage policy)."""
    from storage import db

    cached = db.geocode_get(query)
    if cached is not None:
        return cached
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "jsonv2", "limit": limit, "polygon_geojson": 1},
        headers={"User-Agent": USER_AGENT}, timeout=20,
    )
    r.raise_for_status()
    out = [
        {"name": x.get("display_name"), "lat": float(x["lat"]), "lon": float(x["lon"]),
         "type": x.get("type"), "geojson": x.get("geojson")}
        for x in r.json()
    ]
    db.geocode_put(query, out)
    return out


def from_search_result(res: dict[str, Any], radius_km: float | None = None) -> Area:
    """A searched place: its own polygon if it has one, else a circle of radius_km."""
    gj = res.get("geojson")
    if radius_km or not gj or gj.get("type") not in ("Polygon", "MultiPolygon"):
        return from_point(res["lat"], res["lon"], radius_km or 10, name=res["name"].split(",")[0], source="OpenStreetMap search")
    geom, tol = simplify_for_ee(shape(gj))
    return Area(name=res["name"].split(",")[0], geometry=mapping(geom), source="OpenStreetMap (Nominatim)",
                method="search", simplified_m=tol, meta={"full_name": res["name"]})


# ---------------------------------------------------------------------------
# 3. drawn on map
# ---------------------------------------------------------------------------

def from_drawn(geojson: dict[str, Any], name: str = "Drawn area") -> Area:
    g = geojson.get("geometry", geojson)
    return Area(name=name, geometry=g, source="Drawn on map", method="draw")


# ---------------------------------------------------------------------------
# 4. upload a boundary file
# ---------------------------------------------------------------------------

SUPPORTED_UPLOADS = (".zip", ".shp", ".kml", ".kmz", ".geojson", ".json", ".gpkg")


def read_boundary_file(path: str | Path):
    """Read a boundary file into a GeoDataFrame in EPSG:4326."""
    import geopandas as gpd

    p = Path(path)
    ext = p.suffix.lower()
    if ext not in SUPPORTED_UPLOADS:
        raise ValueError(f"Unsupported file type {ext}. Use Shapefile (.zip), KML/KMZ, GeoJSON or GeoPackage.")
    if ext == ".kmz":
        tmp = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(p) as z:
            kmls = [n for n in z.namelist() if n.lower().endswith(".kml")]
            if not kmls:
                raise ValueError("The KMZ file contains no KML.")
            z.extract(kmls[0], tmp)
            p, ext = tmp / kmls[0], ".kml"
    if ext == ".zip":
        gdf = gpd.read_file(f"zip://{p}")
    elif ext == ".kml":
        gdf = gpd.read_file(p, driver="KML") if _has_driver("KML") else gpd.read_file(p)
    else:
        gdf = gpd.read_file(p)
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    gdf = gdf.to_crs(4326)
    gdf = gdf[gdf.geometry.notna() & gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    if gdf.empty:
        raise ValueError("No polygon features found in the file.")
    return gdf.reset_index(drop=True)


def _has_driver(name: str) -> bool:
    try:
        import pyogrio

        return name in pyogrio.list_drivers()
    except Exception:
        return False


def feature_labels(gdf) -> list[str]:
    """Readable label per feature for the feature picker."""
    name_cols = [c for c in gdf.columns if c.lower() in ("name", "district", "dist_name", "adm2_name", "adm1_name",
                                                         "state", "st_nm", "dtname", "shapename", "nam")]
    labels = []
    for i, row in gdf.iterrows():
        lab = next((str(row[c]) for c in name_cols if row.get(c) not in (None, "")), f"Feature {i + 1}")
        labels.append(lab)
    return labels


def from_upload(path: str | Path, feature_indexes: list[int] | None = None, name: str | None = None) -> Area:
    gdf = read_boundary_file(path)
    sel = gdf if not feature_indexes else gdf.iloc[feature_indexes]
    geom = sel.geometry.union_all() if hasattr(sel.geometry, "union_all") else sel.geometry.unary_union
    geom, tol = simplify_for_ee(geom)
    labels = feature_labels(sel)
    nm = name or (labels[0] if len(labels) == 1 else f"{Path(path).stem} ({len(labels)} features)")
    return Area(name=nm, geometry=mapping(geom), source=f"Uploaded: {Path(path).name}", method="upload",
                simplified_m=tol, meta={"features": labels})


# ---------------------------------------------------------------------------
# 5. coordinates + radius
# ---------------------------------------------------------------------------

def from_point(lat: float, lon: float, radius_km: float, name: str | None = None, source: str = "Coordinates") -> Area:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Latitude must be between -90 and 90 and longitude between -180 and 180.")
    if radius_km <= 0:
        raise ValueError("Radius must be more than 0 km.")
    # Exact circle in a local azimuthal equidistant projection.
    aeqd = f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m"
    to_m = Transformer.from_crs("EPSG:4326", aeqd, always_xy=True).transform
    to_ll = Transformer.from_crs(aeqd, "EPSG:4326", always_xy=True).transform
    x0, y0 = to_m(lon, lat)
    ring = Point(x0, y0).buffer(radius_km * 1000, quad_segs=32)
    xs, ys = ring.exterior.coords.xy
    lons, lats = to_ll(list(xs), list(ys))
    from shapely.geometry import Polygon

    circle = Polygon(zip(lons, lats))
    nm = name or f"{abs(lat):.3f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.3f}°{'E' if lon >= 0 else 'W'} within {radius_km:g} km"
    return Area(name=nm, geometry=mapping(circle), source=source, method="coords",
                meta={"lat": lat, "lon": lon, "radius_km": radius_km})


def to_geojson_feature(area: Area) -> str:
    return json.dumps({"type": "Feature", "properties": {"name": area.name, "source": area.source},
                       "geometry": area.geometry})
