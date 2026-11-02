"""Probe: weighted-arithmetic WQI on the live 12.x columns; derived SGI vs the old SGI. Read-only."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from dotenv import dotenv_values
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
url = dotenv_values(ROOT / ".env")["SUPABASE_DB_URL"]
th = json.load(open(ROOT / "app/core/thresholds.json", encoding="utf-8"))
G = th["wq_guidelines"]["params"]
print(json.dumps(G, ensure_ascii=False)[:1500])
Q = {
    "ph": ("12.2_chemical_water_quality_master", "pH"),
    "do": ("12.2_chemical_water_quality_master", "Dissolved Oxygen (DO)"),
    "bod": ("12.2_chemical_water_quality_master", "BOD5"),
    "cod": ("12.2_chemical_water_quality_master", "COD"),
    "nitrate": ("12.2_chemical_water_quality_master", "Nitrate (NO3-N)"),
    "ammonia": ("12.2_chemical_water_quality_master", "Ammonia (NH3-N)"),
    "phosphate": ("12.2_chemical_water_quality_master", "Orthophosphate (PO43-)"),
    "sulphate": ("12.2_chemical_water_quality_master", "Sulphate"),
    "fluoride": ("12.2_chemical_water_quality_master", "Fluoride"),
    "ec": ("12.1_physical_water_quality_master", "Electrical Conductivity (EC)"),
    "turbidity": ("12.1_physical_water_quality_master", "Turbidity"),
    "tds": ("12.1_physical_water_quality_master", "Total Dissolved Solids (TDS)"),
    "ecoli": ("12.3_biological_water_quality_master", "E. coli"),
    "chla": ("12.3_biological_water_quality_master", "Chlorophyll-a"),
}
with psycopg.connect(url, autocommit=True) as conn:
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    frames = {}
    for k, (t, col) in Q.items():
        rows = conn.execute(
            f'SELECT lower(district_name), make_date(year, month, 1), "{col}" FROM "{t}" WHERE year=2026 AND month=9'
        ).fetchall()
        frames[k] = pd.Series({r[0]: float(r[2]) if r[2] is not None else np.nan for r in rows})
    dep = conn.execute(
        """SELECT lower(district_name), date_trunc('month',date)::date, avg(value) FROM "10_1_borehole_and_aquifer_master" WHERE variable_name='groundwater_table_depth_m_bgl' AND date<='2026-09-30' GROUP BY 1,2"""
    ).fetchall()
    old = conn.execute(
        """SELECT lower(district_name), date_trunc('month',date)::date, max(sgi_dimensionless) FROM hydrological_drought_master WHERE date BETWEEN '2002-05-01' AND '2024-08-31' GROUP BY 1,2"""
    ).fetchall()
X = pd.DataFrame(frames)
print(X.describe().T[["min", "mean", "max"]])
gd = {g["id"]: g for g in G}


def S(k):
    g = gd[k]
    return g.get("max") if k != "do" else g.get("min")


w = {k: 1 / S(k) for k in X}
tot = sum(w.values())
print("weights", {k: round(v / tot * 100, 1) for k, v in w.items()})


def q(k, v):
    if k == "ph":
        return 100 * abs(v - 7) / (gd[k]["max"] - 7)
    if k == "do":
        return max(0, 100 * (14.6 - v) / (14.6 - S(k)))
    return 100 * v / S(k)


wa = sum(X[k].map(lambda v: q(k, v)) * w[k] for k in X) / tot
print("WAWQI describe", wa.describe())
print("100-WAWQI", (100 - wa).describe())
print({k: round(float(X[k].map(lambda v: q(k, v)).mean()), 1) for k in X})
# SGI
D = pd.DataFrame(dep, columns=["d", "m", "v"])
D["m"] = pd.to_datetime(D["m"])
D["v"] = D["v"].astype(float)
idx = pd.date_range("2000-01-01", "2026-09-01", freq="MS")
out = {}
for d, g in D.groupby("d"):
    s = g.set_index("m")["v"].reindex(idx)
    raw_gap = s.isna().mean()
    i = s.interpolate(limit_area="inside")
    # longest gap
    runs = s.isna().astype(int).groupby(s.notna().cumsum()).sum().max()
    z = pd.Series(index=i.index, dtype=float)
    for mo in range(1, 13):
        x = -i[i.index.month == mo]
        r = x.rank()
        z[x.index] = stats.norm.ppf((r - 0.5) / len(x))
    out[d] = (z, raw_gap, runs, s)
print("raw gaps", np.mean([v[1] for v in out.values()]), "longest gap", max(v[2] for v in out.values()))
O = pd.DataFrame(old, columns=["d", "m", "v"])
O["m"] = pd.to_datetime(O["m"])
O["v"] = O["v"].astype(float)
cs = []
ac = []
acraw = []
for d, g in O.groupby("d"):
    if d in out:
        z = out[d][0]
        a = g.set_index("m")["v"].reindex(z.index)
        cs.append(a.corr(z))
        ac.append(z.autocorr(1))
print(
    "r(derived, old SGI) median",
    np.nanmedian(cs),
    "min/max",
    np.nanmin(cs),
    np.nanmax(cs),
    "lag1 derived",
    np.nanmedian(ac),
)
last = pd.Series({d: v[0].iloc[-1] for d, v in out.items()})
print(last.describe())
print(last.loc[[k for k in ("city of cape town", "vhembe", "alfred nzo") if k in last.index]])
print(
    "sampled-only lag1",
    np.nanmedian([v[3].autocorr(1) for v in out.values()]),
    "year-to-year (12 lag)",
    np.nanmedian([v[0].autocorr(12) for v in out.values()]),
)
