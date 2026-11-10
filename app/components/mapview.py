"""Interactive map: base maps, area outline, result layers (Earth Engine tiles or PNG overlays), drawing."""
from __future__ import annotations

import base64
import logging
from pathlib import Path

import ipyleaflet as L
import solara
from ipywidgets import Layout

log = logging.getLogger(__name__)

BASEMAPS = {
    "Street": L.basemaps.OpenStreetMap.Mapnik,
    "Satellite": L.basemaps.Esri.WorldImagery,
    "Terrain": L.basemaps.OpenTopoMap,
}


def ee_tile_url(ee_image, vis: dict) -> str:
    m = ee_image.getMapId(vis)
    return m["tile_fetcher"].url_format


def _png_data_url(path: str) -> str:
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


@solara.component
def MapView(area=None, layers=None, on_draw=None, height: str = "520px", draw: bool = False):
    """area: core.area.Area; layers: list[MapLayer]; on_draw(geojson) when the user draws a shape."""
    base_name, set_base = solara.use_state("Street")

    def make_map():
        m = L.Map(center=(26.2, 92.9), zoom=6, scroll_wheel_zoom=True, layout=Layout(height=height, width="100%"))
        m.add(L.ScaleControl(position="bottomleft", imperial=False))
        m.add(L.FullScreenControl())
        m.add(L.LayersControl(position="topright"))
        return m

    m = solara.use_memo(make_map, dependencies=[])

    # base map
    def set_basemap():
        for lyr in list(m.layers):
            if getattr(lyr, "base", False):
                m.remove(lyr)
        bl = L.basemap_to_tiles(BASEMAPS[base_name])
        bl.base = True
        bl.name = base_name
        m.layers = (bl,) + tuple(x for x in m.layers if not getattr(x, "base", False))

    solara.use_effect(set_basemap, [base_name])

    # drawing
    def setup_draw():
        if not draw:
            return None
        dc = L.DrawControl(
            polygon={"shapeOptions": {"color": "#c92a2a", "fillOpacity": 0.1}},
            rectangle={"shapeOptions": {"color": "#c92a2a", "fillOpacity": 0.1}},
            circle={"shapeOptions": {"color": "#c92a2a", "fillOpacity": 0.1}},
            polyline={}, circlemarker={}, marker={},
        )

        def handle(target, action, geo_json):
            if action == "created" and on_draw:
                g = dict(geo_json)
                # Circles arrive as a point with a radius; convert to a polygon.
                if g.get("geometry", {}).get("type") == "Point" and g.get("properties", {}).get("style", {}).get("radius"):
                    from core.area import from_point

                    lon, lat = g["geometry"]["coordinates"]
                    r_km = g["properties"]["style"]["radius"] / 1000
                    on_draw(from_point(lat, lon, r_km, name="Drawn circle", source="Drawn on map").geometry)
                else:
                    on_draw(g.get("geometry", g))

        dc.on_draw(handle)
        m.add(dc)

        def cleanup():
            try:
                m.remove(dc)
            except Exception:
                pass
        return cleanup

    solara.use_effect(setup_draw, [draw])

    # area outline + result layers
    def update_layers():
        keep = [x for x in m.layers if getattr(x, "base", False)]
        new = []
        if area is not None:
            gj = L.GeoJSON(data={"type": "Feature", "geometry": area.geometry, "properties": {}},
                           style={"color": "#c92a2a", "weight": 2, "fillOpacity": 0.04}, name="Study area")
            new.append(gj)
            w, s, e, n = area.bounds
            try:
                m.fit_bounds([[s, w], [n, e]])
            except Exception:
                pass
        for lyr in layers or []:
            try:
                if lyr.ee_image is not None:
                    url = lyr.tile_url or ee_tile_url(lyr.ee_image, lyr.vis)
                    lyr.tile_url = url
                    new.append(L.TileLayer(url=url, name=lyr.name, opacity=0.85, visible=lyr.shown, attribution="Google Earth Engine"))
                elif lyr.png_path and lyr.bounds:
                    w, s, e, n = lyr.bounds
                    new.append(L.ImageOverlay(url=_png_data_url(lyr.png_path), bounds=((s, w), (n, e)), name=lyr.name, opacity=0.85))
            except Exception:
                log.exception("Could not add layer %s", lyr.name)
        m.layers = tuple(keep + new)

    solara.use_effect(update_layers, [area, tuple(id(x) for x in (layers or []))])

    with solara.Column(gap="4px"):
        solara.ToggleButtonsSingle(value=base_name, values=list(BASEMAPS), on_value=set_base, dense=True)
        solara.display(m)
        if layers:
            for lyr in layers:
                if lyr.legend:
                    Legend(lyr)


@solara.component
def Legend(layer):
    with solara.Row(gap="6px", style={"flex-wrap": "wrap", "align-items": "center", "font-size": "0.85em"}):
        solara.Text(f"{layer.legend_title or layer.name}:", style={"font-weight": "600"})
        if layer.continuous and layer.vis.get("palette"):
            pal = [c if c.startswith("#") else f"#{c}" for c in layer.vis["palette"]]
            grad = f"linear-gradient(to right, {', '.join(pal)})"
            solara.Text(str(layer.vis.get("min", "")))
            solara.HTML(tag="div", unsafe_innerHTML="", style={"width": "160px", "height": "12px", "background": grad, "border": "1px solid #aaa"})
            solara.Text(str(layer.vis.get("max", "")))
        else:
            for item in layer.legend:
                solara.HTML(tag="span", unsafe_innerHTML=f'<span style="display:inline-block;width:12px;height:12px;background:{item.color};'
                            f'border:1px solid #888;margin-right:4px;vertical-align:middle"></span>{item.label}')
