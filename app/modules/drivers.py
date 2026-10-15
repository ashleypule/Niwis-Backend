"""Monthly driver series shared by the vegetation and runoff pages (`timeseries?metric=drivers&var=…`).

Each driver comes from its canonical source, over the window's complete months of *that* source (so the satellite
soil moisture ends 2025-12 while rainfall runs to the latest complete month). Daily variables are aggregated per
district-month in SQL, then area-weighted:

- `mean` drivers (Tmax, ET₀) are means of the days present (ET₀ as mm/day: `meteorological_drought_master` lacks
  31 December, so a December sum would be one day short);
- `sum` drivers (AET) are reported only for months with every day present.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.core.aggregate import is_complete_month, weighted
from app.core.palettes import SERIES
from app.core.provenance import iso
from app.core.stats import rnd
from app.modules.common import Ctx
from app.modules.rainfall.service import RainfallService, _months
from app.schemas import contract as c


@dataclass(frozen=True)
class Driver:
    var: str  # registry variable
    how: str  # "rain" (the rainfall module's monthly totals), "mean", "sum" (daily vars) or "monthly"
    name: str
    unit: str
    axis_label: str
    kind: str
    decimals: int
    color: str


DRIVERS: dict[str, Driver] = {
    "rain": Driver("rain_daily", "rain", "Rainfall", "mm", "Rainfall", "bar", 1, SERIES[0]),
    "temp": Driver("tmax", "mean", "Tmax (monthly mean)", "°C", "Max temperature", "line", 1, SERIES[3]),
    "pet": Driver("et0_daily", "mean", "ET₀ (mean daily)", "mm/day", "Reference ET₀", "line", 2, SERIES[2]),
    "aet": Driver("aet_daily", "sum", "Actual ET", "mm", "Actual evapotranspiration", "line", 1, SERIES[1]),
    "sm": Driver(
        "soil_moisture_surface", "monthly", "Surface soil moisture", "m³/m³", "Soil moisture", "line", 3, SERIES[5]
    ),
}


async def _last_complete_month(ctx: Ctx, var: str) -> dt.date:
    newest = await ctx.data.newest(var)
    m = newest.replace(day=1)
    return m if is_complete_month(m, newest) else (pd.Timestamp(m) - pd.offsets.MonthBegin(1)).date()


async def driver_series(ctx: Ctx, rain: RainfallService, key: str, sid: str = "drivers") -> c.SeriesSet:
    d = DRIVERS[key]
    w = ctx.weights()
    start, _ = await rain.window()
    if d.how == "rain":
        data = (await rain.ts_monthly_series())["rain"]
        last = await rain.latest_complete_month()
    else:
        if d.how == "monthly":
            df = await ctx.data.monthly(d.var)
            last = pd.Timestamp(df["date"].max()).date()
        else:
            dm = await ctx.data.daily_monthly_stats(d.var, ())
            last = await _last_complete_month(ctx, d.var)
            if d.how == "mean":
                df = dm.assign(value=dm["sum"] / dm["days"])
            else:
                full = dm["date"].map(lambda t: calendar.monthrange(t.year, t.month)[1])
                df = dm.assign(value=dm["sum"].where(dm["days"] >= full))
        agg = weighted(df[["district", "date", "value"]], w).set_index("date")["value"]
        months = _months(min(start.replace(day=1), last), last)
        data = [[iso(m.date()), rnd(agg.get(m), d.decimals)] for m in months]
    prov: dict[str, Any] = ctx.provenance(last, [ctx.data.table_of(d.var)], res="monthly")
    prov.pop("asOf")
    return c.SeriesSet(
        id=sid,
        title=d.axis_label,
        x_type="time",
        axes=[c.Axis(unit=d.unit, label=d.axis_label)],
        series=[c.Series(key=key, name=d.name, kind=d.kind, color=d.color, data=data)],
        as_of=iso(last),
        note=f"Complete months of the source table, area-weighted; {ctx.scope.name}.",
        **prov,
    )
