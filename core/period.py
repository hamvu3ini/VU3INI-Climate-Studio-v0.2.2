"""Analysis period, season filter, baseline and time step, with dataset date checks."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from . import datasets

STEPS = ("daily", "monthly", "seasonal", "yearly")
MONSOON = (6, 7, 8, 9)


@dataclass
class Period:
    start: date
    end: date
    months: tuple[int, ...] = ()            # season filter; empty = all months
    baseline_start: int = 1991              # years, inclusive
    baseline_end: int = 2020
    step: str = "monthly"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"], d["end"] = self.start.isoformat(), self.end.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Period":
        return cls(
            start=date.fromisoformat(d["start"]), end=date.fromisoformat(d["end"]),
            months=tuple(d.get("months") or ()), baseline_start=int(d.get("baseline_start", 1991)),
            baseline_end=int(d.get("baseline_end", 2020)), step=d.get("step", "monthly"),
        )

    @property
    def label(self) -> str:
        s = f"{self.start:%d %b %Y} – {self.end:%d %b %Y}"
        if self.months:
            s += f" (months {season_label(self.months)})"
        return s

    @property
    def short(self) -> str:
        return f"{self.start:%Y%m}-{self.end:%Y%m}" if self.start.year != self.end.year or self.start.month != self.end.month else f"{self.start:%Y%m%d}-{self.end:%Y%m%d}"

    @property
    def baseline_label(self) -> str:
        return f"{self.baseline_start}–{self.baseline_end}"

    def month_starts(self) -> list[date]:
        """First day of every month in the period that passes the season filter."""
        out = []
        d = self.start.replace(day=1)
        while d <= self.end:
            if not self.months or d.month in self.months:
                out.append(d)
            d += relativedelta(months=1)
        return out

    def years(self) -> list[int]:
        return list(range(self.start.year, self.end.year + 1))


def season_label(months: tuple[int, ...]) -> str:
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if not months:
        return "all"
    ms = sorted(months)
    if ms == list(range(ms[0], ms[-1] + 1)):
        return f"{names[ms[0] - 1]}–{names[ms[-1] - 1]}"
    return ", ".join(names[m - 1] for m in ms)


def preset(name: str, today: date | None = None) -> Period:
    today = today or date.today()
    if name == "last12":
        start = (today.replace(day=1) - relativedelta(months=12))
        return Period(start, today)
    if name == "last3y":
        return Period(date(today.year - 3, today.month, 1), today)
    if name == "monsoon":
        y = today.year if today.month >= 6 else today.year - 1
        return Period(date(y, 6, 1), min(date(y, 9, 30), today), months=MONSOON)
    raise ValueError(name)


def check(period: Period, dataset_keys: list[str], latest: dict[str, date | None] | None = None,
          needs_separate_baseline: bool = False, today: date | None = None) -> Period:
    """Validate against dataset ranges. Returns a (possibly trimmed) copy with warnings.

    `latest` maps dataset key -> newest available date (from Earth Engine), if known.
    """
    today = today or date.today()
    p = Period(period.start, period.end, period.months, period.baseline_start, period.baseline_end, period.step, [])
    if p.end < p.start:
        p.start, p.end = p.end, p.start
        p.warnings.append("The From and To dates were swapped.")
    if p.end > today:
        p.end = today
    for key in dataset_keys:
        d = datasets.get(key)
        if p.start < d.start:
            p.warnings.append(f"{d.name} starts on {d.start:%d %b %Y}; the period was trimmed to begin then.")
            p.start = d.start
        if p.baseline_start < d.start.year + (0 if d.start.month == 1 and d.start.day <= 2 else 1):
            new = d.start.year + (0 if d.start.month == 1 and d.start.day <= 2 else 1)
            p.warnings.append(f"{d.name} has no full year of data before {new}; the baseline now starts in {new}.")
            p.baseline_start = new
        newest = (latest or {}).get(key)
        if newest is None and d.lag_days:
            newest = today - timedelta(days=d.lag_days)
            approx = " (approximate)"
        else:
            approx = ""
        if newest and p.end > newest:
            p.warnings.append(f"Latest available {d.name} data{approx}: {newest:%d %b %Y}. Results stop there.")
            p.end = newest
    if p.baseline_end < p.baseline_start:
        p.baseline_end = p.baseline_start
    if needs_separate_baseline and not (p.end.year < p.baseline_start or p.start.year > p.baseline_end):
        p.warnings.append("The baseline overlaps the analysis period. Anomalies are clearer when they are separate.")
    if p.end < p.start:
        p.warnings.append("No data is available for the chosen dates.")
    return p
