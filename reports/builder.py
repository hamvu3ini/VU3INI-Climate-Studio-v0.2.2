"""Report engine: one content model -> HTML preview (and print), Word (.docx) and PDF.

Usage:
    opts = ReportOptions()
    assets = prepare_assets(result, opts)        # renders maps and charts to PNG
    html_path = to_html(result, assets, opts)    # preview / print
    docx_path = to_docx(result, assets, opts)
    pdf_path  = to_pdf(result, assets, opts)
"""
from __future__ import annotations

import base64
import logging
import re
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from core import paths
from core.notice import NOTICE_FULL, NOTICE_SHORT
from core.result import AnalysisResult
from core.settings import Branding

from . import static_charts

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).resolve().parent / "templates"

SECTIONS = {
    "cover": "Cover",
    "summary": "Summary",
    "area": "Study area",
    "results": "Results (maps, charts, table)",
    "method": "Method",
    "ai": "AI explanation",
    "notes": "Notes, limitations and data sources",
}


@dataclass
class ReportOptions:
    sections: dict[str, bool] = field(default_factory=lambda: {k: True for k in SECTIONS})
    page_size: str = "A4"            # A4 | Letter
    landscape: bool = False
    branding: Branding = field(default_factory=Branding)
    language: str = "en"
    title: str = ""


@dataclass
class Assets:
    maps: list[tuple[str, str]] = field(default_factory=list)      # (title, png path)
    charts: list[tuple[str, str]] = field(default_factory=list)
    locator: str = ""
    logo: str = ""
    folder: str = ""


def file_stem(res: AnalysisResult) -> str:
    """<analysis>_<area>_<from>-<to>, e.g. VCI_Dibrugarh_2024-2026."""
    short = {"drought_vci": "VCI", "drought_temporal": "DroughtClasses", "drought_modis": "VHI", "precip_forecast": "RainForecast",
             "precip_sdii": "SDII", "precip_anomaly": "RainAnomaly", "precip_cluster": "RainZones"}.get(
        res.analysis_id, "".join(w.capitalize() for w in res.analysis_id.replace("station_", "Station_").split("_")))
    area = "".join(w[:1].upper() + w[1:] for w in re.split(r"[^A-Za-z0-9]+", res.area_name.split(",")[0]) if w)[:30] or "Area"
    yrs = re.findall(r"\b(?:19|20)\d{2}\b", res.period_label)
    span = f"{yrs[0]}-{yrs[-1]}" if yrs and yrs[0] != yrs[-1] else (yrs[0] if yrs else datetime.now().strftime("%Y"))
    return f"{short}_{area}_{span}"


def prepare_assets(res: AnalysisResult, opts: ReportOptions | None = None, out_dir: Path | None = None) -> Assets:
    opts = opts or ReportOptions()
    folder = out_dir or (Path(res.saved_dir) / "report_assets" if res.saved_dir else paths.cache_dir() / f"assets_{datetime.now():%Y%m%d%H%M%S}")
    folder.mkdir(parents=True, exist_ok=True)
    a = Assets(folder=str(folder))
    logo = Path(opts.branding.logo_path) if opts.branding.logo_path else paths.LOGO_PATH
    a.logo = str(logo) if logo.exists() else ""
    # charts
    for i, c in enumerate(res.charts):
        p = folder / f"chart_{i}.png"
        try:
            static_charts.to_png(c, str(p))
            c.png_path = str(p)
            a.charts.append((c.title, str(p)))
        except Exception:  # noqa: BLE001
            log.exception("Chart %s failed", c.title)
    # maps (Earth Engine) or saved PNGs
    from shapely.geometry import shape

    bounds = shape(res.area_geometry).bounds if res.area_geometry else None
    for i, m in enumerate(res.map_layers):
        if m.png_path and Path(m.png_path).exists():
            a.maps.append((m.legend_title or m.name, m.png_path))
            continue
        if m.ee_image is None or bounds is None or not (m.shown or i == 0):
            continue
        p = folder / f"map_{i}.png"
        try:
            from . import mapimg

            mapimg.render(m, res.area_geometry, bounds, str(p), title=m.legend_title or m.name,
                          source_note=f"Background: SRTM hillshade. Boundary: {res.area_source}.")
            m.png_path = str(p)
            a.maps.append((m.legend_title or m.name, str(p)))
        except Exception:  # noqa: BLE001
            log.exception("Map %s failed", m.name)
    if res.area_geometry and opts.sections.get("area", True):
        p = folder / "locator.png"
        try:
            from core import ee_client

            from . import mapimg

            if ee_client.is_ready():
                mapimg.render_locator(res.area_geometry, bounds, str(p))
            else:
                mapimg.render_outline_only(res.area_geometry, str(p))
            a.locator = str(p)
        except Exception:  # noqa: BLE001
            log.exception("Locator map failed")
            try:
                from . import mapimg

                mapimg.render_outline_only(res.area_geometry, str(p))
                a.locator = str(p)
            except Exception:  # noqa: BLE001
                pass
    return a


# ---------------------------------------------------------------------------
# HTML preview and print
# ---------------------------------------------------------------------------

def _b64(path: str) -> str:
    if not path or not Path(path).exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def _table_html(df: pd.DataFrame | None) -> str:
    if df is None or df.empty:
        return ""
    return df.to_html(index=False, classes="stats", border=0, na_rep="–", float_format=lambda v: f"{v:,.2f}".rstrip("0").rstrip("."))


def context(res: AnalysisResult, assets: Assets, opts: ReportOptions) -> dict:
    method_rows = []
    for k in ("formula", "steps", "datasets", "baseline", "parameters", "scale", "device"):
        v = res.method.get(k)
        if not v:
            continue
        if isinstance(v, (list, tuple)):
            v = "; ".join(map(str, v))
        elif isinstance(v, dict):
            v = ", ".join(f"{a} = {b}" for a, b in v.items())
        method_rows.append((k.capitalize(), str(v)))
    if res.device_used and not res.method.get("device"):
        method_rows.append(("Device", res.device_used))
    return {
        "r": res, "o": opts, "s": opts.sections, "b": opts.branding,
        "title": opts.title or res.title,
        "generated": datetime.now().strftime("%d %b %Y %H:%M"),
        "logo": _b64(assets.logo), "locator": _b64(assets.locator),
        "maps": [(t, _b64(p)) for t, p in assets.maps], "charts": [(t, _b64(p)) for t, p in assets.charts],
        "stats_html": _table_html(res.stats), "method_rows": method_rows,
        "notice_short": NOTICE_SHORT, "notice_full": NOTICE_FULL,
        "page": f"{opts.page_size} {'landscape' if opts.landscape else 'portrait'}",
    }


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))


def to_html(res: AnalysisResult, assets: Assets, opts: ReportOptions | None = None, path: Path | None = None,
            auto_print: bool = False) -> Path:
    opts = opts or ReportOptions()
    html = _env().get_template("report.html").render(**context(res, assets, opts), auto_print=auto_print)
    path = path or paths.reports_dir() / f"{file_stem(res)}{'_print' if auto_print else ''}.html"
    path.write_text(html, encoding="utf-8")
    return path


def open_print(res: AnalysisResult, assets: Assets, opts: ReportOptions | None = None) -> Path:
    """Open the preview in the browser with the print dialog."""
    p = to_html(res, assets, opts, auto_print=True)
    webbrowser.open(p.as_uri())
    return p


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

def _add_page_number(paragraph) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    for code in ("PAGE",):
        r = paragraph.add_run()
        f1 = OxmlElement("w:fldChar")
        f1.set(qn("w:fldCharType"), "begin")
        it = OxmlElement("w:instrText")
        it.set(qn("xml:space"), "preserve")
        it.text = code
        f2 = OxmlElement("w:fldChar")
        f2.set(qn("w:fldCharType"), "end")
        r._r.append(f1)
        r._r.append(it)
        r._r.append(f2)


def _docx_table(doc, df: pd.DataFrame):
    from docx.shared import Pt

    t = doc.add_table(rows=1, cols=len(df.columns))
    t.style = "Light Grid Accent 1"
    for i, c in enumerate(df.columns):
        t.rows[0].cells[i].text = str(c)
    for _, row in df.iterrows():
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = "–" if pd.isna(v) else (f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v))
    for row in t.rows:
        for c in row.cells:
            for p in c.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9)
    return t


def to_docx(res: AnalysisResult, assets: Assets, opts: ReportOptions | None = None, path: Path | None = None) -> Path:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    opts = opts or ReportOptions()
    s = opts.sections
    doc = Document()
    sec = doc.sections[0]
    if opts.page_size == "A4":
        sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    else:
        sec.page_width, sec.page_height = Cm(21.59), Cm(27.94)
    if opts.landscape:
        sec.orientation = WD_ORIENT.LANDSCAPE
        sec.page_width, sec.page_height = sec.page_height, sec.page_width
    for side in ("left_margin", "right_margin"):
        setattr(sec, side, Cm(2))
    sec.top_margin, sec.bottom_margin = Cm(2), Cm(2)
    width = sec.page_width - sec.left_margin - sec.right_margin
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)

    # footer: notice + page number
    fp = sec.footer.paragraphs[0]
    fp.text = f"{NOTICE_SHORT}   Page "
    fp.runs[0].font.size = Pt(7.5)
    fp.runs[0].font.color.rgb = RGBColor(0x55, 0x55, 0x55)
    _add_page_number(fp)

    title = opts.title or res.title
    if s.get("cover", True):
        if assets.logo:
            doc.add_picture(assets.logo, width=Cm(3.2))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.LEFT
        doc.add_heading(title, level=0)
        for lab, val in [("Area", f"{res.area_name} ({res.area_source})"), ("Period", res.period_label),
                         ("Baseline", res.baseline_label), ("Generated", datetime.now().strftime("%d %b %Y %H:%M")),
                         ("Prepared by", ", ".join(x for x in (opts.branding.author, opts.branding.organisation, opts.branding.contact) if x))]:
            if val:
                p = doc.add_paragraph()
                p.add_run(f"{lab}: ").bold = True
                p.add_run(val)
        if res.info_what:
            p = doc.add_paragraph()
            p.add_run("What this analysis does: ").bold = True
            p.add_run(res.info_what)
        if res.info_example:
            p = doc.add_paragraph()
            p.add_run("Easy example: ").bold = True
            p.add_run(res.info_example + " (illustration)")
    if s.get("summary", True) and res.summary:
        doc.add_heading("Summary", level=1)
        doc.add_paragraph(res.summary)
        for w in res.warnings:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run("Note: ").italic = True
            p.add_run(w)
    if s.get("area", True) and (assets.locator or res.area_geometry):
        doc.add_heading("Study area", level=1)
        doc.add_paragraph(f"{res.area_name}. Boundary source: {res.area_source}.")
        if assets.locator:
            doc.add_picture(assets.locator, width=Cm(7))
    if s.get("results", True):
        doc.add_heading("Results", level=1)
        for t, p in assets.maps:
            doc.add_picture(p, width=width)
            doc.add_paragraph(t, style="Caption")
        if res.stats is not None and not res.stats.empty:
            doc.add_heading(res.stats_title, level=2)
            _docx_table(doc, res.stats)
        for t, p in assets.charts:
            doc.add_picture(p, width=width)
            doc.add_paragraph(t, style="Caption")
    if s.get("method", True) and res.method:
        doc.add_heading("Method", level=1)
        for k, v in context(res, assets, opts)["method_rows"]:
            p = doc.add_paragraph()
            p.add_run(f"{k}: ").bold = True
            p.add_run(v)
    if s.get("ai", True) and res.ai_explanation:
        doc.add_heading("AI-generated explanation", level=1)
        doc.add_paragraph(res.ai_explanation)
        doc.add_paragraph("Generated by an AI model from the numbers above; check before relying on it.").runs[0].italic = True
    if s.get("notes", True):
        doc.add_heading("Notes, limitations and data sources", level=1)
        for x in res.limitations:
            doc.add_paragraph(x, style="List Bullet")
        if res.citations:
            doc.add_paragraph("Data sources and references:").runs[0].bold = True
            for c in res.citations:
                doc.add_paragraph(c, style="List Bullet")
        if res.tutorial_url:
            doc.add_paragraph(f"Method tutorial: {res.tutorial_url}")
        p = doc.add_paragraph()
        p.add_run("Personal-use notice: ").bold = True
        p.add_run(NOTICE_FULL)
    path = path or paths.reports_dir() / f"{file_stem(res)}.docx"
    doc.save(path)
    return path


# ---------------------------------------------------------------------------
# PDF (ReportLab, embedded DejaVu Sans font)
# ---------------------------------------------------------------------------

_FONTS_DONE = False


def _register_fonts() -> tuple[str, str]:
    global _FONTS_DONE
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if not _FONTS_DONE:
        import matplotlib

        base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        bundled = Path(__file__).resolve().parent / "fonts"
        reg = bundled / "NotoSans-Regular.ttf"
        bold = bundled / "NotoSans-Bold.ttf"
        if not (reg.exists() and bold.exists()):
            reg, bold = base / "DejaVuSans.ttf", base / "DejaVuSans-Bold.ttf"
        pdfmetrics.registerFont(TTFont("Body", str(reg)))
        pdfmetrics.registerFont(TTFont("Body-Bold", str(bold)))
        from reportlab.lib.fonts import addMapping

        addMapping("Body", 0, 0, "Body")
        addMapping("Body", 1, 0, "Body-Bold")
        addMapping("Body", 0, 1, "Body")
        addMapping("Body", 1, 1, "Body-Bold")
        _FONTS_DONE = True
    return "Body", "Body-Bold"


def to_pdf(res: AnalysisResult, assets: Assets, opts: ReportOptions | None = None, path: Path | None = None) -> Path:
    from xml.sax.saxutils import escape

    from PIL import Image as PILImage
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, LETTER, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    opts = opts or ReportOptions()
    s = opts.sections
    body, bold = _register_fonts()
    size = A4 if opts.page_size == "A4" else LETTER
    if opts.landscape:
        size = landscape(size)
    path = path or paths.reports_dir() / f"{file_stem(res)}.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=size, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                            bottomMargin=2 * cm, title=opts.title or res.title, author=opts.branding.author,
                            subject=res.analysis_id, creator="VU3INI Climate Studio")
    ss = getSampleStyleSheet()
    st = {
        "body": ParagraphStyle("b", parent=ss["Normal"], fontName=body, fontSize=10, leading=14),
        "small": ParagraphStyle("s", parent=ss["Normal"], fontName=body, fontSize=8, leading=10.5, textColor=colors.HexColor("#555555")),
        "h0": ParagraphStyle("h0", parent=ss["Title"], fontName=bold, fontSize=20, leading=24, alignment=0, spaceAfter=10),
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName=bold, fontSize=14, leading=18, spaceBefore=12, spaceAfter=6,
                             textColor=colors.HexColor("#1b4f8a")),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName=bold, fontSize=11.5, leading=15, spaceBefore=8, spaceAfter=4),
        "cap": ParagraphStyle("c", parent=ss["Normal"], fontName=body, fontSize=8.5, leading=11, textColor=colors.HexColor("#444444"),
                              spaceAfter=10),
        "cell": ParagraphStyle("cell", parent=ss["Normal"], fontName=body, fontSize=8.2, leading=10),
        "cellb": ParagraphStyle("cellb", parent=ss["Normal"], fontName=bold, fontSize=8.2, leading=10, textColor=colors.white),
    }
    W = size[0] - 4 * cm

    def P(text, style="body"):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), st[style])

    def kv(label, value):
        return Paragraph(f"<b>{escape(label)}:</b> {escape(str(value))}", st["body"])

    def img(p, width):
        with PILImage.open(p) as im:
            w, h = im.size
        height = width * h / w
        max_h = size[1] - 6 * cm
        if height > max_h:
            width, height = width * max_h / height, max_h
        return Image(p, width=width, height=height)

    flow = []
    title = opts.title or res.title
    if s.get("cover", True):
        if assets.logo:
            flow.append(img(assets.logo, 3.2 * cm))
            flow.append(Spacer(1, 6))
        flow.append(P(title, "h0"))
        for lab, val in [("Area", f"{res.area_name} ({res.area_source})"), ("Period", res.period_label), ("Baseline", res.baseline_label),
                         ("Generated", datetime.now().strftime("%d %b %Y %H:%M")),
                         ("Prepared by", ", ".join(x for x in (opts.branding.author, opts.branding.organisation, opts.branding.contact) if x))]:
            if val:
                flow.append(kv(lab, val))
        flow.append(Spacer(1, 8))
        if res.info_what:
            flow.append(kv("What this analysis does", res.info_what))
        if res.info_example:
            flow.append(kv("Easy example", res.info_example + " (illustration)"))
    if s.get("summary", True) and res.summary:
        flow += [P("Summary", "h1"), P(res.summary)]
        for w in res.warnings:
            flow.append(P(f"Note: {w}", "small"))
    if s.get("area", True) and assets.locator:
        flow += [P("Study area", "h1"), P(f"{res.area_name}. Boundary source: {res.area_source}."), Spacer(1, 4), img(assets.locator, 7 * cm)]
    if s.get("results", True):
        flow.append(P("Results", "h1"))
        for t, p in assets.maps:
            flow.append(KeepTogether([img(p, W), P(t, "cap")]))
        if res.stats is not None and not res.stats.empty:
            df = res.stats
            data = [[Paragraph(escape(str(c)), st["cellb"]) for c in df.columns]]
            for _, row in df.iterrows():
                data.append([Paragraph(escape("–" if pd.isna(v) else (f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v))),
                                       st["cell"]) for v in row])
            tbl = Table(data, repeatRows=1, colWidths=[W / len(df.columns)] * len(df.columns))
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1b4f8a")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f3f5")]),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#ced4da")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            flow += [P(res.stats_title, "h2"), tbl, Spacer(1, 10)]
        for t, p in assets.charts:
            flow.append(KeepTogether([img(p, W), P(t, "cap")]))
    if s.get("method", True) and res.method:
        flow.append(P("Method", "h1"))
        for k, v in context(res, assets, opts)["method_rows"]:
            flow.append(kv(k, v))
    if s.get("ai", True) and res.ai_explanation:
        flow += [P("AI-generated explanation", "h1"), P(res.ai_explanation),
                 P("Generated by an AI model from the numbers above; check before relying on it.", "small")]
    if s.get("notes", True):
        flow.append(P("Notes, limitations and data sources", "h1"))
        for x in res.limitations:
            flow.append(P(f"• {x}"))
        if res.citations:
            flow.append(Spacer(1, 4))
            flow.append(P("Data sources and references", "h2"))
            for c in res.citations:
                flow.append(P(f"• {c}", "small"))
        if res.tutorial_url:
            flow.append(P(f"Method tutorial: {res.tutorial_url}", "small"))
        flow += [Spacer(1, 8), kv("Personal-use notice", NOTICE_FULL)]

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont(body, 7.2)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(2 * cm, 1.1 * cm, NOTICE_SHORT)
        canvas.drawRightString(size[0] - 2 * cm, 1.1 * cm, f"Page {d.page}")
        canvas.drawString(2 * cm, size[1] - 1.1 * cm, "VU3INI Climate Studio")
        canvas.drawRightString(size[0] - 2 * cm, size[1] - 1.1 * cm, title[:90])
        canvas.restoreState()

    doc.build(flow, onFirstPage=footer, onLaterPages=footer)
    return path
