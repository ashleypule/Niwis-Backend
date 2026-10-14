"""Provenance and staleness.

Every value the API returns carries the date of the newest record it was computed from (`asOf`) and the tables it
came from (`source`). A value is stale when its `asOf` is more than `STALE_AFTER_DAYS` (default 90) older than the
newest date anywhere in the database — e.g. drought-page VCI from `satellite_remote_sensing` (ends 2025-12) while
rainfall runs to 2026-09.
"""

from __future__ import annotations

import datetime as dt


def is_stale(as_of: dt.date | None, newest: dt.date | None, limit_days: int) -> bool:
    """True when `as_of` lags the newest database date by more than `limit_days`. Unknown dates are not stale."""
    if as_of is None or newest is None:
        return False
    return (newest - as_of).days > limit_days


def iso(d: dt.date | dt.datetime | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, dt.datetime):
        d = d.date()
    return d.isoformat()
