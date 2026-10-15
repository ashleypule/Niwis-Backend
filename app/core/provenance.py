"""Provenance and staleness.

Every value the API returns carries the date of the newest record it was computed from (`asOf`) and the tables it
came from (`source`). `asOf` keeps the contract's period-start stamping (monthly `YYYY-MM-01`, annual `YYYY-01-01`).

Staleness is measured from the **end** of the value's period (`periodEnd`): daily = that day, monthly = the last day of
the month, annual = 31 December. A value is stale when its period ended more than `STALE_AFTER_DAYS` (default 90)
before the newest date anywhere in the database — e.g. drought-page VCI from `satellite_remote_sensing` (ends 2025-12)
while rainfall runs to 2026-09. A value for the latest *complete* period relative to that newest date is never stale:
with data to 2026-09-06, annual values for 2025 and monthly values for August 2026 are current, because nothing newer
can exist yet.
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Literal

Resolution = Literal["daily", "monthly", "annual"]


def period_end(d: dt.date, resolution: Resolution) -> dt.date:
    """Last day of the period that starts (or ends) on `d`."""
    if resolution == "monthly":
        return d.replace(day=calendar.monthrange(d.year, d.month)[1])
    if resolution == "annual":
        return dt.date(d.year, 12, 31)
    return d


def latest_complete_period_end(newest: dt.date, resolution: Resolution) -> dt.date:
    """End of the latest period that is complete on `newest` (the newest date in the database)."""
    if resolution == "monthly":
        return newest if newest == period_end(newest, "monthly") else newest.replace(day=1) - dt.timedelta(days=1)
    if resolution == "annual":
        return newest if (newest.month, newest.day) == (12, 31) else dt.date(newest.year - 1, 12, 31)
    return newest


def is_stale(as_of: dt.date | None, newest: dt.date | None, limit_days: int, resolution: Resolution = "daily") -> bool:
    """True when the period of `as_of` ended more than `limit_days` before `newest`, unless it is the latest complete
    period. Unknown dates are not stale."""
    if as_of is None or newest is None:
        return False
    end = period_end(as_of, resolution)
    if end >= latest_complete_period_end(newest, resolution):
        return False
    return (newest - end).days > limit_days


def iso(d: dt.date | dt.datetime | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, dt.datetime):
        d = d.date()
    return d.isoformat()
