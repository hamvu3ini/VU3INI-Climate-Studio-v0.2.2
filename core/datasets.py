"""Registry of Earth Engine datasets used by the app.

IDs, bands and start dates are starting points. `verify_all()` checks each one
against the live catalog on the user's PC; run it once after connecting
(Settings > Check datasets) and fix any entry it flags.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Dataset:
    key: str
    ee_id: str
    name: str
    bands: tuple[str, ...]
    scale_m: int
    start: date
    cadence: str  # daily, 8-day, 16-day, monthly, hourly, 6-hourly, static
    citation: str
    kind: str = "ImageCollection"  # or Image, FeatureCollection
    notes: str = ""
    lag_days: int = 0  # typical delay before newest data appears


DATASETS: dict[str, Dataset] = {d.key: d for d in [
    Dataset("modis_ndvi", "MODIS/061/MOD13Q1", "MODIS Terra Vegetation Indices 16-day 250 m",
            ("NDVI", "EVI", "SummaryQA"), 250, date(2000, 2, 18), "16-day",
            "Didan, K. (2021). MODIS/Terra Vegetation Indices 16-Day L3 Global 250m SIN Grid V061. NASA EOSDIS LP DAAC.",
            lag_days=20),
    Dataset("modis_ndvi_1km", "MODIS/061/MOD13A2", "MODIS Terra Vegetation Indices 16-day 1 km",
            ("NDVI", "EVI", "SummaryQA"), 1000, date(2000, 2, 18), "16-day",
            "Didan, K. (2021). MOD13A2 V061. NASA EOSDIS LP DAAC.", lag_days=20),
    Dataset("modis_lst8", "MODIS/061/MOD11A2", "MODIS Terra Land Surface Temperature 8-day 1 km",
            ("LST_Day_1km", "QC_Day", "LST_Night_1km", "QC_Night"), 1000, date(2000, 3, 5), "8-day",
            "Wan, Z., Hook, S., Hulley, G. (2021). MOD11A2 V061. NASA EOSDIS LP DAAC.", lag_days=12),
    Dataset("modis_lst1", "MODIS/061/MOD11A1", "MODIS Terra Land Surface Temperature daily 1 km",
            ("LST_Day_1km", "QC_Day", "LST_Night_1km", "QC_Night"), 1000, date(2000, 2, 24), "daily",
            "Wan, Z., Hook, S., Hulley, G. (2021). MOD11A1 V061. NASA EOSDIS LP DAAC.", lag_days=5),
    Dataset("modis_snow", "MODIS/061/MOD10A1", "MODIS Terra Snow Cover daily 500 m",
            ("NDSI_Snow_Cover",), 500, date(2000, 2, 24), "daily",
            "Hall, D. K., Riggs, G. A. (2021). MOD10A1 V61. NSIDC.", lag_days=5),
    Dataset("modis_et", "MODIS/061/MOD16A2", "MODIS Terra Evapotranspiration 8-day 500 m",
            ("ET", "PET"), 500, date(2001, 1, 1), "8-day",
            "Running, S., Mu, Q., Zhao, M., Moreno, A. (2021). MOD16A2 V061. NASA EOSDIS LP DAAC.", lag_days=30),
    Dataset("chirps", "UCSB-CHG/CHIRPS/DAILY", "CHIRPS daily rainfall (about 5.5 km)",
            ("precipitation",), 5566, date(1981, 1, 1), "daily",
            "Funk, C. et al. (2015). The climate hazards infrared precipitation with stations. Scientific Data 2, 150066.",
            notes="Check the catalog for a newer CHIRPS version (v3) and switch if available.", lag_days=45),
    Dataset("era5_land_daily", "ECMWF/ERA5_LAND/DAILY_AGGR", "ERA5-Land daily aggregates (about 11 km)",
            ("temperature_2m", "temperature_2m_min", "temperature_2m_max", "total_precipitation_sum",
             "volumetric_soil_water_layer_1"), 11132, date(1950, 1, 2), "daily",
            "Muñoz Sabater, J. (2019). ERA5-Land hourly data from 1950 to present. Copernicus C3S CDS.", lag_days=10),
    Dataset("gfs", "NOAA/GFS0P25", "NOAA GFS 0.25° forecasts",
            ("total_precipitation_surface", "temperature_2m_above_ground"), 27830, date(2015, 7, 1), "6-hourly",
            "NOAA National Centers for Environmental Prediction, Global Forecast System.",
            notes="VERIFY: total_precipitation_surface is treated as accumulated from the run start to each forecast hour."),
    Dataset("srtm", "USGS/SRTMGL1_003", "SRTM elevation 30 m", ("elevation",), 30, date(2000, 2, 11), "static",
            "Farr, T. G. et al. (2007). The Shuttle Radar Topography Mission. Rev. Geophys. 45.", kind="Image"),
    Dataset("s1_grd", "COPERNICUS/S1_GRD", "Sentinel-1 SAR GRD", ("VV", "VH"), 10, date(2014, 10, 3), "6-12 days",
            "Contains modified Copernicus Sentinel data, processed by ESA.", lag_days=3),
    Dataset("s2_l1c", "COPERNICUS/S2_HARMONIZED", "Sentinel-2 MSI Level-1C (harmonized)",
            ("B2", "B3", "B4", "B8A", "B11", "B12", "QA60"), 10, date(2015, 6, 23), "5 days",
            "Contains modified Copernicus Sentinel data, processed by ESA.", lag_days=2),
    Dataset("s2_sr", "COPERNICUS/S2_SR_HARMONIZED", "Sentinel-2 MSI Level-2A (harmonized)",
            ("B2", "B3", "B4", "B8", "B8A", "B11", "B12", "SCL"), 10, date(2017, 3, 28), "5 days",
            "Contains modified Copernicus Sentinel data, processed by ESA.", lag_days=3),
    Dataset("jrc_water", "JRC/GSW1_4/GlobalSurfaceWater", "JRC Global Surface Water",
            ("occurrence",), 30, date(1984, 3, 16), "static",
            "Pekel, J.-F. et al. (2016). High-resolution mapping of global surface water. Nature 540.", kind="Image"),
    Dataset("smap", "NASA/SMAP/SPL4SMGP/007", "SMAP L4 soil moisture (9 km, 3-hourly)",
            ("sm_surface", "sm_rootzone"), 11000, date(2015, 3, 31), "3-hourly",
            "Reichle, R. et al. SMAP L4 Global 3-hourly 9 km EASE-Grid Surface and Root Zone Soil Moisture. NSIDC.",
            notes="VERIFY: SMAP L4 version number changes; use the newest in the catalog.", lag_days=5),
    Dataset("gaul0", "FAO/GAUL/2015/level0", "FAO GAUL 2015 countries", ("ADM0_NAME",), 0, date(2015, 1, 1), "static",
            "FAO GAUL 2015.", kind="FeatureCollection",
            notes="Boundaries are from 2015 and may differ from official national boundaries."),
    Dataset("gaul1", "FAO/GAUL/2015/level1", "FAO GAUL 2015 first-level units", ("ADM0_NAME", "ADM1_NAME"), 0,
            date(2015, 1, 1), "static", "FAO GAUL 2015.", kind="FeatureCollection"),
    Dataset("gaul2", "FAO/GAUL/2015/level2", "FAO GAUL 2015 second-level units",
            ("ADM0_NAME", "ADM1_NAME", "ADM2_NAME"), 0, date(2015, 1, 1), "static", "FAO GAUL 2015.",
            kind="FeatureCollection"),
]}

# Boundary sources for "Pick from list". geoBoundaries lives in the community
# catalog; VERIFY the asset path on the user's PC (Settings > Check datasets).
BOUNDARY_SOURCES = {
    "FAO GAUL 2015": {
        0: ("FAO/GAUL/2015/level0", "ADM0_NAME"),
        1: ("FAO/GAUL/2015/level1", "ADM1_NAME"),
        2: ("FAO/GAUL/2015/level2", "ADM2_NAME"),
        "parents": {1: ["ADM0_NAME"], 2: ["ADM0_NAME", "ADM1_NAME"]},
    },
}


def get(key: str) -> Dataset:
    return DATASETS[key]


def latest_date(key: str) -> date | None:
    """Date of the newest image in the collection (needs Earth Engine)."""
    import ee

    d = DATASETS[key]
    if d.kind != "ImageCollection":
        return None
    try:
        ms = (ee.ImageCollection(d.ee_id)
              .filterDate(ee.Date(datetime.now(timezone.utc).isoformat()).advance(-400, "day"), ee.Date("2100-01-01"))
              .aggregate_max("system:time_start").getInfo())
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date() if ms else None
    except Exception:
        log.exception("latest_date failed for %s", key)
        return None


@dataclass
class CheckResult:
    key: str
    ok: bool
    message: str
    missing_bands: list[str] = field(default_factory=list)


def verify_all() -> list[CheckResult]:
    """Check every dataset ID and band against the live catalog."""
    import ee

    out = []
    for d in DATASETS.values():
        try:
            if d.kind == "ImageCollection":
                first = ee.ImageCollection(d.ee_id).first()
                bands = first.bandNames().getInfo()
            elif d.kind == "Image":
                bands = ee.Image(d.ee_id).bandNames().getInfo()
            else:
                bands = ee.FeatureCollection(d.ee_id).first().propertyNames().getInfo()
            missing = [b for b in d.bands if b not in bands]
            out.append(CheckResult(d.key, not missing, "OK" if not missing else f"Missing: {missing}", missing))
        except Exception as e:  # noqa: BLE001
            out.append(CheckResult(d.key, False, f"Not found or no access: {e}"))
    return out
