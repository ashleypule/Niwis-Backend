"""Opt-in cross-check on the real Supabase database: the runoff anomaly served from
`7.2_derived_runoff_indicators_master` against the API's former own calculation (same-calendar-month mean of complete
months 2000–last full year of the modelled runoff depth). `pytest -m integration -s` prints how far they differ
(DATA_GAPS §20: r ≈ 0.998, mean |difference| ≈ 2.5 percentage points when recorded)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import psycopg
import pytest

from app.config import Settings
from app.core.aggregate import monthly_normals_pct

pytestmark = pytest.mark.integration


def own_normals(depth: pd.DataFrame, y1: int) -> pd.DataFrame:
    """The API's former runoff normal: same calendar month, complete months 2000–y1. Columns district, month, normal."""
    d = depth[depth["date"].dt.year.between(2000, y1) & (depth["days"] >= depth["date"].dt.days_in_month)]
    return (
        d.assign(month=d["date"].dt.month)
        .groupby(["district", "month"], as_index=False)["value"]
        .mean()
        .rename(columns={"value": "normal"})
    )


def test_stored_runoff_anomaly_against_the_api_calculation() -> None:
    url = Settings().supabase_db_url
    if url is None:
        pytest.skip("SUPABASE_DB_URL not set")
    with psycopg.connect(url.get_secret_value(), autocommit=True) as c:
        c.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        c.execute("SET statement_timeout = 600000")
        depth = pd.DataFrame(
            c.execute(
                'SELECT "District_Name", make_date("Year"::int, "Month"::int, 1), '
                'sum("Daily_Surface_Runoff_Volume_m3_day") / 6.5e9 * 1000, count(*) '
                "FROM runoff_variables_master GROUP BY 1, 2"
            ).fetchall(),
            columns=["district", "date", "value", "days"],
        )
        stored = pd.DataFrame(
            c.execute(
                'SELECT "District_Name", make_date("Year"::int, "Month"::int, 1), "Runoff_Anomaly_percent" '
                'FROM "7.2_derived_runoff_indicators_master"'
            ).fetchall(),
            columns=["district", "date", "value"],
        )
    depth["date"], stored["date"] = pd.to_datetime(depth["date"]), pd.to_datetime(stored["date"])
    depth[["value", "days"]] = depth[["value", "days"]].astype(float)
    stored["value"] = stored["value"].astype(float)
    complete = depth[depth["days"] >= depth["date"].dt.days_in_month]
    y1 = int(complete["date"].dt.year.max()) - 1

    def anomaly(normals: pd.DataFrame) -> pd.Series:
        m = complete.assign(month=complete["date"].dt.month).merge(normals, on=["district", "month"])
        return m.set_index(["district", "date"]).eval("(value - normal) / normal * 100")

    x = pd.concat(
        [
            stored.set_index(["district", "date"])["value"].rename("stored"),
            anomaly(own_normals(depth, y1)).rename("own"),
            anomaly(monthly_normals_pct(complete[["district", "date", "value"]], stored)).rename("served"),
        ],
        axis=1,
    )
    x = x.replace([np.inf, -np.inf], np.nan).dropna()
    r, mad = x["own"].corr(x["stored"]), (x["own"] - x["stored"]).abs().mean()
    p95 = (x["own"] - x["stored"]).abs().quantile(0.95)
    served_mad = (x["served"] - x["stored"]).abs().mean()
    print(
        f"\nrunoff anomaly, {len(x)} district-months: API own 2000–{y1} vs 7.2: r = {r:.4f}, "
        f"mean |diff| = {mad:.2f} pp, 95th pct {p95:.2f} pp; served (stored normals) vs 7.2: {served_mad:.3f} pp"
    )
    assert served_mad < 0.2  # the served multi-district anomaly reproduces the table
    assert r > 0.99 and mad < 5  # the two baselines differ only by 2026 being in 7.2's mean
