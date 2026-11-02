"""Probe: does a depth-derived SGI (10_1) behave like a groundwater drought index? Read-only."""

from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from dotenv import dotenv_values
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
url = dotenv_values(ROOT / ".env")["SUPABASE_DB_URL"]
with psycopg.connect(url, autocommit=True) as conn:
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    dep = conn.execute(
        """SELECT lower(district_name), date_trunc('month',date)::date, avg(value) FROM "10_1_borehole_and_aquifer_master" WHERE variable_name='groundwater_table_depth_m_bgl' AND date<='2026-09-30' GROUP BY 1,2"""
    ).fetchall()
    old = conn.execute(
        """SELECT lower(district_name), date_trunc('month',date)::date, max(sgi_dimensionless) FROM hydrological_drought_master WHERE date BETWEEN '2002-05-01' AND '2024-08-31' GROUP BY 1,2"""
    ).fetchall()
    spi = (
        conn.execute(
            """SELECT lower(district_name), date_trunc('month',date)::date, max("SPI_12"), max("SPI_24") FROM "6.2_meteorological_drought_indices" WHERE date<='2026-08-31' GROUP BY 1,2"""
        ).fetchall()
        if False
        else None
    )
    tbl = conn.execute(
        """SELECT table_name, column_name FROM information_schema.columns WHERE column_name ILIKE 'spi_12%' """
    ).fetchall()
    print(tbl)
    t, col = tbl[0]
    cols = {
        r[0]
        for r in conn.execute(
            f"""SELECT column_name FROM information_schema.columns WHERE table_name='{t}'"""
        ).fetchall()
    }
    dcol = "date" if "date" in cols else "Date"
    spi = conn.execute(
        f"""SELECT lower("District_Name"), date_trunc('month',"{dcol}")::date, max("{col}") FROM "{t}" WHERE "{dcol}"<='2026-08-31' GROUP BY 1,2"""
    ).fetchall()
idx = pd.date_range("2000-01-01", "2026-09-01", freq="MS")


def wide(rows):
    d = pd.DataFrame(rows, columns=["d", "m", "v"])
    d["m"] = pd.to_datetime(d["m"])
    d["v"] = pd.to_numeric(d["v"], errors="coerce").astype(float)
    return d.pivot_table(index="m", columns="d", values="v").reindex(idx)


D, O, S = wide(dep), wide(old), wide(spi)
I = D.interpolate(limit_area="inside")


def sgi(col):
    z = pd.Series(index=col.index, dtype=float)
    for mo in range(1, 13):
        x = -col[col.index.month == mo]
        r = x.rank()
        z[x.index] = stats.norm.ppf((r - 0.5) / x.notna().sum())
    return z


Z = I.apply(sgi)
common = [c for c in Z.columns if c in O.columns and c in S.columns]


def rs(a, b):
    return pd.Series({c: a[c].corr(b[c]) for c in common})


for nm, a, b in (("derived vs old", Z, O), ("derived vs SPI-12", Z, S), ("old vs SPI-12", O, S)):
    r = rs(a, b)
    print(nm, "median", round(r.median(), 3), "min", round(r.min(), 3), "max", round(r.max(), 3))
# lags: groundwater lags rainfall
for lag in (3, 6, 12, 24):
    print(
        "derived vs SPI-12 lag",
        lag,
        round(rs(Z, S.shift(lag)).median(), 3),
        "old",
        round(rs(O, S.shift(lag)).median(), 3),
    )
sampled = D.notna()
print(
    "sampled-only derived vs SPI-12",
    round(pd.Series({c: Z[c][sampled[c]].corr(S[c][sampled[c]]) for c in common}).median(), 3),
)
print(
    "persistence on samples ~49d apart:",
    round(np.median([D[c].dropna().autocorr(1) for c in D]), 3),
    "after removing district mean + calendar mean",
)
A = D.copy()
for c in A:
    A[c] = A[c] - A[c].groupby(A.index.month).transform("mean")
print(
    "deseasonalised sample-to-sample autocorr:",
    round(np.median([A[c].dropna().autocorr(1) for c in A]), 3),
    "lag 7 samples (~1yr):",
    round(np.median([A[c].dropna().autocorr(7) for c in A]), 3),
)
print("year means (all districts plain mean):")
y = (
    pd.DataFrame({"derived": Z.mean(axis=1), "old": O.mean(axis=1), "spi12": S.mean(axis=1)})
    .groupby(lambda t: t.year)
    .mean()
    .round(2)
)
print(y.to_string())
print("district ranges of depth (m):", (D.max() - D.min()).describe().round(2).to_dict())
print(
    "between-district corr of Z in same month (median):",
    round(np.nanmedian(Z.corr().values[np.triu_indices(52, 1)]), 3),
    "old:",
    round(np.nanmedian(O.corr().values[np.triu_indices(O.shape[1], 1)]), 3),
)
