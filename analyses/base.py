"""Analysis framework: parameters, registry, tutorial links, common result set-up."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.area import Area
from core.period import Period
from core.result import AnalysisResult
from i18n import t

log = logging.getLogger(__name__)

# Tutorial videos from the original list (number -> URL).
TUTORIALS = {
    1: "https://www.youtube.com/watch?v=TwesbdGkL1M",
    2: "https://www.youtube.com/watch?v=gQ2UM_1ikbk",
    3: "https://www.youtube.com/watch?v=EUpbZOSU2k8",
    4: "https://www.youtube.com/watch?v=l2szl6JeTOE",
    5: "https://www.youtube.com/watch?v=xOwCpvnQmd8",
    6: "https://www.youtube.com/watch?v=i1TkiMMK-OU",
    7: "https://www.youtube.com/watch?v=KPgIv_sndTI",
    8: "https://www.youtube.com/watch?v=6vYv0ETQ_0E",
    9: "https://www.youtube.com/watch?v=QvfN85UgPLA",
    10: "https://www.youtube.com/watch?v=8gicfUEh_bU",
    11: "https://www.youtube.com/watch?v=6jfEKSIWvWo",
    12: "https://www.youtube.com/watch?v=30u8DuY0EfU",
    13: "https://www.youtube.com/watch?v=juRsxppLd7I",
    14: "https://www.youtube.com/watch?v=JiHRDfTOU-w",
    15: "https://www.youtube.com/watch?v=GqHvvpxy4rE",
    16: "https://www.youtube.com/watch?v=YYZ3Fjgaqhl",
    17: "https://www.youtube.com/watch?v=7ib0GoOMCBo",
}
# Some IDs were read from a phone screenshot where capital I and lowercase l look
# identical (tutorials 4, 7, 11 and 16). If a link fails, swap that letter.


@dataclass
class Param:
    key: str
    label: str
    default: Any
    kind: str = "float"                  # int | float | choice | bool
    choices: list[Any] | None = None
    min: float | None = None
    max: float | None = None
    help: str = ""


Progress = Callable[[float, str], None]


class Cancelled(Exception):
    pass


@dataclass
class RunContext:
    progress: Progress | None = None
    cancel_flag: Callable[[], bool] | None = None
    started: float = field(default_factory=time.time)

    def step(self, frac: float, msg: str) -> None:
        if self.cancel_flag and self.cancel_flag():
            raise Cancelled()
        log.info("[%3.0f%%] %s", frac * 100, msg)
        if self.progress:
            self.progress(frac, msg)


class Analysis:
    id: str = ""
    tutorial_no: int | None = None
    module: str = ""                     # Drought | Rainfall | Heat & cold | ...
    datasets: list[str] = []
    params: list[Param] = []
    needs_separate_baseline: bool = False
    phase: int = 1

    @property
    def title(self) -> str:
        return t(f"analysis.{self.id}.title") if t(f"analysis.{self.id}.title") != f"analysis.{self.id}.title" else self.default_title

    default_title: str = ""

    def defaults(self) -> dict[str, Any]:
        return {p.key: p.default for p in self.params}

    def clean_params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        out = self.defaults()
        for p in self.params:
            if params and p.key in params and params[p.key] is not None:
                v = params[p.key]
                if p.kind == "int":
                    v = int(v)
                elif p.kind == "float":
                    v = float(v)
                elif p.kind == "bool":
                    v = bool(v)
                if p.min is not None and isinstance(v, (int, float)):
                    v = max(p.min, v)
                if p.max is not None and isinstance(v, (int, float)):
                    v = min(p.max, v)
                out[p.key] = v
        return out

    def new_result(self, area: Area, period: Period, params: dict[str, Any]) -> AnalysisResult:
        return AnalysisResult(
            analysis_id=self.id, title=self.default_title, area_name=area.name, area_source=area.source,
            period_label=period.label, baseline_label=period.baseline_label, params=params,
            info_what=t(f"info.{self.id}.what"), info_example=t(f"info.{self.id}.example"),
            tutorial_url=TUTORIALS.get(self.tutorial_no or 0, ""), warnings=list(period.warnings) + area.warnings(),
            area_geometry=area.geometry,
        )

    def run(self, area: Area, period: Period, params: dict[str, Any] | None = None,
            ctx: RunContext | None = None) -> AnalysisResult:
        raise NotImplementedError


REGISTRY: dict[str, Analysis] = {}


def register(cls: type[Analysis]) -> type[Analysis]:
    REGISTRY[cls.id] = cls()
    return cls


def by_module() -> dict[str, list[Analysis]]:
    out: dict[str, list[Analysis]] = {}
    for a in REGISTRY.values():
        out.setdefault(a.module, []).append(a)
    return out
