"""App-wide reactive state."""
from __future__ import annotations

from datetime import date

import solara

from core import settings as settings_mod
from core.period import Period, preset

settings = solara.reactive(settings_mod.load())

# Earth Engine
ee_status = solara.reactive("not_checked")       # Status value
ee_detail = solara.reactive("")

# Analyses page
area = solara.reactive(None)                      # core.area.Area | None
period = solara.reactive(preset("last3y"))
analysis_id = solara.reactive("drought_vci")
params = solara.reactive({})
result = solara.reactive(None)                    # AnalysisResult | None
progress = solara.reactive(0.0)
progress_msg = solara.reactive("")
error = solara.reactive("")
cancel_requested = solara.reactive(False)

# Station page
station_result = solara.reactive(None)
station_name = solara.reactive("Premises station")

# Map
drawn_geojson = solara.reactive(None)


def save_settings(s: settings_mod.Settings) -> None:
    settings_mod.save(s)
    # Force a new object so components re-render.
    settings.set(settings_mod.load())


def today() -> date:
    return date.today()


def reset_result() -> None:
    result.set(None)
    error.set("")
    progress.set(0.0)
    progress_msg.set("")


__all__ = ["settings", "area", "period", "analysis_id", "params", "result", "Period"]
