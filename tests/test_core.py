"""Unit tests that run without Earth Engine or a GPU.

Run:  .venv\\Scripts\\python -m pytest -q        (Windows)
"""
from __future__ import annotations

import math
import os
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("VCS_DATA_DIR", tempfile.mkdtemp(prefix="vcs_test_"))

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


# ---------------------------------------------------------------------------
# core
# ---------------------------------------------------------------------------

def test_project_id_rules():
    from core.ee_client import valid_project_id

    assert valid_project_id("vu3ini-climate")
    assert valid_project_id("ee-sasanka123")
    assert not valid_project_id("Vu3ini")          # uppercase
    assert not valid_project_id("abc")             # too short
    assert not valid_project_id("climate-")        # ends with hyphen
    assert not valid_project_id("1climate")        # starts with digit


def test_error_classification():
    from core.ee_client import Status, classify_error

    assert classify_error(Exception("Project is not registered to use Earth Engine")).status == Status.NOT_REGISTERED
    assert classify_error(Exception("Earth Engine API has not been used in project 123")).status == Status.NOT_REGISTERED
    assert classify_error(Exception("invalid_grant: Token has been expired")).status == Status.TOKEN_EXPIRED


def test_circle_area():
    from core.area import from_point

    a = from_point(27.47, 94.91, 25)
    assert a.area_km2 == pytest.approx(math.pi * 25 ** 2, rel=0.01)
    lat, lon = a.centroid
    assert lat == pytest.approx(27.47, abs=0.01) and lon == pytest.approx(94.91, abs=0.01)
    with pytest.raises(ValueError):
        from_point(95, 0, 10)


def test_large_area_warning():
    from core.area import from_point

    a = from_point(25, 90, 500)
    assert any("very large" in w for w in a.warnings())
    assert a.suggested_scale(250) >= 1000


def test_upload_geojson(tmp_path):
    import json

    from core.area import from_upload

    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "Test district"},
         "geometry": {"type": "Polygon", "coordinates": [[[94.0, 27.0], [95.0, 27.0], [95.0, 28.0], [94.0, 28.0], [94.0, 27.0]]]}}]}
    p = tmp_path / "d.geojson"
    p.write_text(json.dumps(gj))
    a = from_upload(p)
    assert a.name == "Test district"
    assert 9000 < a.area_km2 < 12000
    assert a.source == "Uploaded: d.geojson"


def test_period_checks():
    from core.period import Period, check

    p = check(Period(date(1995, 1, 1), date(2026, 10, 31)), ["modis_ndvi"], needs_separate_baseline=True, today=date(2026, 11, 1))
    assert p.start == date(2000, 2, 18)
    assert p.baseline_start == 2001
    assert any("overlaps" in w for w in p.warnings)
    p2 = check(Period(date(2026, 1, 1), date(2025, 1, 1)), ["chirps"], today=date(2026, 11, 1))
    assert p2.start < p2.end


def test_period_months_and_season():
    from core.period import Period

    p = Period(date(2024, 1, 15), date(2026, 10, 31), months=(6, 7, 8, 9))
    ms = p.month_starts()
    assert len(ms) == 12 and all(d.month in (6, 7, 8, 9) for d in ms)


def test_result_roundtrip(tmp_path):
    from core.result import AnalysisResult, ChartSpec, LegendItem, MapLayer

    r = AnalysisResult("drought_vci", "VCI", "Dibrugarh", "Test", "Jan 2024 – Oct 2026", summary="ok",
                       stats=pd.DataFrame({"a": [1, 2]}),
                       charts=[ChartSpec("c", "bar", pd.DataFrame({"x": ["a", "b"], "y": [1, 2]}), "x", ["y"])],
                       map_layers=[MapLayer("m", None, {"min": 0}, [LegendItem("x", "#000000")])])
    d = r.save(tmp_path)
    r2 = AnalysisResult.load(d)
    assert r2.summary == "ok" and len(r2.charts) == 1 and r2.stats.shape == (2, 1) and r2.map_layers[0].legend[0].label == "x"


# ---------------------------------------------------------------------------
# compute device
# ---------------------------------------------------------------------------

def _fake_hw(gpu: bool, vram: float = 8.0):
    from compute.device import GPUInfo, Hardware

    hw = Hardware(cpu_cores=8, cpu_threads=16, ram_gb=32, torch_version="2.9", torch_cuda="12.8" if gpu else "", cuda_usable=gpu)
    if gpu:
        hw.gpus = [GPUInfo("NVIDIA GeForce RTX 5060", vram, vram, "12.0")]
    return hw


def test_device_choice_cpu_only():
    from compute.device import DeviceManager

    m = DeviceManager("auto", _fake_hw(False))
    assert m.choose("prithvi", 50).kind == "cpu"


def test_device_choice_gpu(monkeypatch):
    from compute import device as dv

    m = dv.DeviceManager("auto", _fake_hw(True))
    monkeypatch.setattr(m, "_free_vram_gb", lambda: 7.5)
    assert m.choose("prithvi", 50).kind == "cuda"
    assert m.choose("prithvi", 50).batch_size == 4
    m.mode = "cpu"
    assert m.choose("prithvi", 50).kind == "cpu"
    m.mode = "auto"
    m.benchmarks = {"prithvi": {"cpu_s": 0.1, "gpu_s": 0.5}}
    assert m.choose("prithvi", 50).kind == "cpu"   # benchmark says CPU is faster


def test_oom_fallback(monkeypatch):
    from compute import device as dv

    m = dv.DeviceManager("auto", _fake_hw(True))
    monkeypatch.setattr(m, "_free_vram_gb", lambda: 12.5)
    calls = []

    def job(d):
        calls.append((d.kind, d.batch_size))
        if d.kind == "cuda":
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
        return "done"

    out, d = m.run_with_fallback("prithvi", job, size=10)
    assert out == "done" and d.kind == "cpu"
    assert calls[0] == ("cuda", 8) and calls[1] == ("cuda", 4) and calls[2] == ("cuda", 2) and calls[-1][0] == "cpu"


def test_blackwell_needs_cuda128():
    from compute.device import _ver

    assert _ver("12.6") < (12, 8) <= _ver("12.8")


# ---------------------------------------------------------------------------
# analyses (local parts)
# ---------------------------------------------------------------------------

def test_spi_standard_normal():
    from analyses.spi import spi_series

    rng = np.random.default_rng(0)
    dates = pd.date_range("1981-01-01", "2020-12-01", freq="MS")
    rain = rng.gamma(2.0, 60.0, len(dates))
    df = pd.DataFrame({"date": dates, "rain_mm": rain})
    out = spi_series(df, "rain_mm", 3, 1981, 2020)
    v = out["SPI"].dropna()
    assert abs(v.mean()) < 0.1 and 0.85 < v.std() < 1.15


def test_drought_events():
    from analyses.drought import drought_events

    df = pd.DataFrame({"label": [f"2025-{m:02d}" for m in range(1, 9)], "VCI": [50, 25, 20, 28, 45, 15, 35, 60]})
    ev = drought_events(df, "VCI", 30)
    assert len(ev) == 2
    assert ev.iloc[0]["Onset"] == "2025-02" and ev.iloc[0]["End"] == "2025-04" and ev.iloc[0]["Peak value"] == 20


def test_registry_has_phase1_analyses():
    import analyses

    for k in ("drought_vci", "drought_temporal", "drought_modis", "precip_forecast", "precip_sdii", "precip_anomaly", "precip_cluster"):
        assert k in analyses.REGISTRY


# ---------------------------------------------------------------------------
# station
# ---------------------------------------------------------------------------

def test_dew_point_and_heat_index():
    from station.derive import dew_point_c, heat_index_c

    assert float(dew_point_c(25, 60)) == pytest.approx(16.7, abs=0.2)
    # NWS table: 90 °F at 70 % RH -> about 106 °F (41.1 °C)
    assert float(heat_index_c((90 - 32) * 5 / 9, 70)) == pytest.approx(41.1, abs=0.8)


def test_fao56_example():
    """FAO-56 Example 18 (Brussels, 6 July): ET0 about 3.9 mm/day."""
    from station.derive import fao56_et0

    et = float(fao56_et0(21.5, 12.3, (63 + 84) / 2, 2.078, 22.07, 50.8, 100, 187))
    assert et == pytest.approx(3.9, abs=0.3)


@pytest.fixture(scope="module")
def samples():
    import sys

    sys.path.insert(0, str(HERE))
    import make_samples

    DATA.mkdir(exist_ok=True)
    d = make_samples.synth(days=40)
    return (make_samples.write_desktop(d, DATA / "t_desktop.txt"), make_samples.write_cloud_csv(d.iloc[:1500], DATA / "t_cloud.csv"))


def test_import_desktop(samples):
    from station import importer

    raw = importer.read_file(samples[0])
    assert raw.kind == "desktop"
    m = importer.guess_mapping(raw)
    for f in ("temp_c", "hum_pct", "rain_mm", "bar_hpa", "solar_wm2", "uv_index", "et_mm", "soil_cb_1", "soil_cb_2", "wind_dir_deg"):
        assert f in m.columns.values(), f
    assert m.date_order == "DMY" and m.units["temp"] == "C"
    recs, _ = importer.convert(raw, m)
    assert len(recs) == 40 * 48
    assert recs.iloc[0]["ts_utc"] == "2026-03-31 18:30:00"   # IST -> UTC


def test_import_cloud_csv_units(samples):
    from station import importer

    raw = importer.read_file(samples[1])
    m = importer.guess_mapping(raw)
    assert m.units == {"temp": "F", "wind": "mph", "rain": "in", "bar": "inHg"}
    assert m.date_order == "MDY"
    recs, _ = importer.convert(raw, m)
    d = importer.convert(importer.read_file(samples[0]), importer.guess_mapping(importer.read_file(samples[0])))[0]
    # Same synthetic weather in both files: converted values must agree.
    assert recs["temp_c"].iloc[:50].mean() == pytest.approx(d["temp_c"].iloc[:50].mean(), abs=0.1)
    assert recs["bar_hpa"].iloc[:50].mean() == pytest.approx(d["bar_hpa"].iloc[:50].mean(), abs=0.2)


def test_qc_flags_spike(samples):
    from station import importer, qc

    raw = importer.read_file(samples[0])
    recs, _ = importer.convert(raw, importer.guess_mapping(raw))
    rep = qc.run(recs)
    assert (rep.flags["field"] == "temp_c").any()
    clean = qc.apply_exclusions(recs, rep.flags)
    assert clean["temp_c"].max() < 45 and clean["heat_index_c"].max() < 60


def test_station_reports_all(samples):
    from station import derive, importer, qc, reports

    raw = importer.read_file(samples[0])
    recs, _ = importer.convert(raw, importer.guess_mapping(raw))
    recs = qc.apply_exclusions(recs, qc.run(recs).flags)
    daily = derive.daily(recs, lat=26.1, elev_m=55)
    avail = reports.available(daily)
    assert len(avail) == len(reports.REPORTS)
    for rid in avail:
        r = reports.build(rid, daily, recs, "Test", date(2026, 4, 1), date(2026, 5, 10))
        assert r.summary and (r.stats is not None)


def test_validation_metrics():
    from station.validation import metrics

    o = pd.Series([1.0, 2, 3, 4, 5])
    k = metrics(o, o * 1.1)
    assert k["pbias_pct"] == pytest.approx(10) and k["r"] == pytest.approx(1)


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------

def test_reports_docx_pdf_html(samples, tmp_path):
    from docx import Document

    from reports import builder
    from station import derive, importer, reports

    raw = importer.read_file(samples[0])
    recs, _ = importer.convert(raw, importer.guess_mapping(raw))
    daily = derive.daily(recs, lat=26.1, elev_m=55)
    res = reports.build("station_wind", daily, recs, "Test station", date(2026, 4, 1), date(2026, 5, 10))
    opts = builder.ReportOptions()
    assets = builder.prepare_assets(res, opts, tmp_path)
    assert len(assets.charts) == len(res.charts)
    h = builder.to_html(res, assets, opts, tmp_path / "r.html")
    d = builder.to_docx(res, assets, opts, tmp_path / "r.docx")
    p = builder.to_pdf(res, assets, opts, tmp_path / "r.pdf")
    assert "Personal, noncommercial use only" in h.read_text(encoding="utf-8")
    doc = Document(d)
    assert "Personal, noncommercial use only" in doc.sections[0].footer.paragraphs[0].text
    assert p.stat().st_size > 20_000 and p.read_bytes()[:4] == b"%PDF"


def test_file_stem():
    from core.result import AnalysisResult
    from reports.builder import file_stem

    assert file_stem(AnalysisResult("drought_vci", "x", "Dibrugarh, Assam", "GAUL", "01 Jan 2024 – 31 Oct 2026")) == "VCI_Dibrugarh_2024-2026"


def test_ai_prompt_has_numbers_only():
    from ai.explain import build_prompt
    from core.result import AnalysisResult

    r = AnalysisResult("precip_sdii", "SDII", "Dhemaji", "GAUL", "2024–2026", summary="s", key_numbers={"mean_sdii": 14.2})
    p = build_prompt(r, "en")
    assert "14.2" in p and "Dhemaji" in p and "English" in p


def test_i18n_fallback():
    from i18n import t

    assert t("nav.home") == "Home"
    assert t("nav.home", lang="fr") == "Home"   # falls back to English until Phase 4 catalogues exist
    assert t("no.such.key") == "no.such.key"
