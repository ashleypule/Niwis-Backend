"""Global filter parameters accepted by every module endpoint (contract "Common query parameters").

Scope rules (decision D1, docs/CONTRACT_CHANGES.md):
- `municipality`: district / metro MDB codes (DC44, CPT …). Local-municipality codes (EC101 …) → 400, because the
  data stop at district level.
- `province`: aggregate of its districts, area-weighted.
- `wma`: area-overlap-weighted aggregate of districts; responses carry `aggregation: "district-overlap"`.
- `catchment`: 501 DATA_UNAVAILABLE (districts are far coarser than catchments).
- Most specific wins: municipality > catchment > wma > province > national.

Period: `period` (default 12M) or `from`/`to` (override). The window is anchored at the newest date of the data being
queried, which services pass to `Window.resolve()`.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Literal

from dateutil.relativedelta import relativedelta

from app.core import districts
from app.core.errors import BadRequest, DataUnavailable

PERIODS: dict[str, relativedelta | None] = {
    "latest": None,
    "1M": relativedelta(months=1),
    "3M": relativedelta(months=3),
    "6M": relativedelta(months=6),
    "12M": relativedelta(months=12),
    "24M": relativedelta(months=24),
    "5Y": relativedelta(years=5),
    "10Y": relativedelta(years=10),
    "historical": None,
}
WMA_IDS = [f"WMA{i}" for i in range(1, 10)]
_LOCAL_MUNI = re.compile(r"^[A-Z]{2,3}\d{3}$", re.I)  # e.g. EC101, NC062, KZN252

ScopeLevel = Literal["national", "province", "wma", "municipality"]


@dataclass(frozen=True)
class ScopeSel:
    level: ScopeLevel
    id: str
    name: str
    district_codes: tuple[
        str, ...
    ]  # districts in scope (for wma: districts that overlap it; weights come from spatial)
    approximate: bool = False  # True for WMA (district-overlap)


@dataclass(frozen=True)
class Window:
    period: str
    date_from: dt.date | None
    date_to: dt.date | None

    def resolve(self, newest: dt.date, oldest: dt.date | None = None) -> tuple[dt.date, dt.date]:
        """Concrete [start, end] for data whose newest date is `newest`."""
        if self.date_from or self.date_to:
            return (self.date_from or oldest or dt.date(1900, 1, 1), self.date_to or newest)
        if self.period == "historical":
            return (oldest or dt.date(1900, 1, 1), newest)
        if self.period == "latest":
            return (newest, newest)
        delta = PERIODS[self.period]
        assert delta is not None
        return (newest - delta + relativedelta(days=1), newest)


@dataclass(frozen=True)
class Filters:
    scope: ScopeSel
    window: Window
    compare: bool
    entity_id: str | None


def _parse_date(name: str, value: str | None) -> dt.date | None:
    if value is None or value == "":
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise BadRequest(
            f"`{name}` must be a date in YYYY-MM-DD format", issues=[{"param": name, "value": value}]
        ) from None


def parse_scope(
    province: str | None = None,
    wma: str | None = None,
    catchment: str | None = None,
    municipality: str | None = None,
    wma_districts: dict[str, tuple[str, ...]] | None = None,
) -> ScopeSel:
    """Validate scope params and return the most specific scope. `wma_districts` comes from app.core.spatial."""
    prov = province.strip().upper() if province else None
    if prov and prov not in districts.PROVINCES:
        raise BadRequest(
            f"Unknown province {province!r}",
            issues=[{"param": "province", "allowed": sorted(districts.PROVINCES)}],
        )

    if municipality:
        code = municipality.strip()
        d = districts.by_code(code)
        if d is None:
            if _LOCAL_MUNI.match(code):
                raise BadRequest(
                    f"Municipality {code!r} is a local municipality; NIWIS data are available per district "
                    "municipality only. Use the parent district's MDB code (see /api/v1/reference/municipalities).",
                    issues=[{"param": "municipality", "value": code, "reason": "below district level"}],
                )
            raise BadRequest(
                f"Unknown municipality {code!r}",
                issues=[
                    {"param": "municipality", "value": code, "hint": "use a district or metro MDB code, e.g. DC44"}
                ],
            )
        if prov and d.province != prov:
            raise BadRequest(
                f"Municipality {d.code} is in {d.province}, not {prov}",
                issues=[{"param": "municipality", "value": d.code, "province": d.province}],
            )
        return ScopeSel("municipality", d.code, d.official_name, (d.code,))

    if catchment:
        raise DataUnavailable(
            "Catchment filtering is not available: NIWIS data are held per district municipality, which is far "
            "coarser than a catchment.",
            missing=[f"catchment:{catchment}"],
        )

    if wma:
        w = wma.strip().upper()
        if w not in WMA_IDS:
            raise BadRequest(f"Unknown WMA {wma!r}", issues=[{"param": "wma", "allowed": WMA_IDS}])
        if wma_districts is None:
            raise DataUnavailable(
                "WMA filtering needs district boundaries to compute the district–WMA overlap; they are not loaded.",
                missing=["district boundaries"],
            )
        return ScopeSel("wma", w, w, wma_districts.get(w, ()), approximate=True)

    if prov:
        return ScopeSel("province", prov, districts.PROVINCES[prov], tuple(d.code for d in districts.in_province(prov)))

    return ScopeSel("national", "ZA", "South Africa", tuple(d.code for d in districts.districts()))


def parse_window(period: str | None, date_from: str | None, date_to: str | None) -> Window:
    p = period or "12M"
    if p not in PERIODS:
        raise BadRequest(f"Unknown period {period!r}", issues=[{"param": "period", "allowed": list(PERIODS)}])
    f, t = _parse_date("from", date_from), _parse_date("to", date_to)
    if f and t and f > t:
        raise BadRequest("`from` must not be after `to`", issues=[{"from": date_from, "to": date_to}])
    return Window(p, f, t)


def parse_compare(compare: str | None) -> bool:
    if compare in (None, "", "0"):
        return False
    if compare == "1":
        return True
    raise BadRequest("`compare` must be 1", issues=[{"param": "compare", "value": compare}])
