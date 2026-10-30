"""Independent SSI-3 recomputation (McKee 1993 / WMO-No. 1090 method) from SQL-extracted monthly series.
Two gamma fits are used: scipy MLE (floc=0) and Thom's (1958) approximate MLE used by the classic SPI programme.
"""
import os, re, sys
import numpy as np, pandas as pd
from scipy import stats
sys.stdout.reconfigure(encoding="utf-8")
S = os.path.dirname(os.path.abspath(__file__))
areas = {}
for m in re.finditer(r"\('([^']*)','([A-Z0-9]+)','([A-Z]+)',([0-9.]+)\)", open(os.path.join(S, "w.sql"), encoding="utf-8").read()):
    areas[m.group(2)] = float(m.group(4))
TOTAL = sum(areas.values())

def parse(s):
    out = {}
    for tok in s.strip().split(","):
        k, v = tok.split(":"); v = v.split("/")[0]
        out[pd.Timestamp(k + "-01")] = float(v)
    return pd.Series(out).sort_index()

def section(path, header_start):
    lines = open(path, encoding="utf-8").read().splitlines()
    i = next(i for i, l in enumerate(lines) if l.startswith("-- " + header_start))
    rows = []
    for l in lines[i + 2:]:
        if l.startswith("-- "): break
        rows.append(l)
    return rows

def thom_fit(x):
    x = np.asarray(x, float)
    A = np.log(x.mean()) - np.log(x).mean()
    a = (1 + np.sqrt(1 + 4 * A / 3)) / (4 * A)
    return a, x.mean() / a

def ssi(series, scale, cal=(2000, 2025), method="mle"):
    s = series.sort_index().astype(float)
    # a complete monthly index (no gaps assumed by the method); reindex to be explicit
    idx = pd.date_range(s.index.min(), s.index.max(), freq="MS")
    s = s.reindex(idx)
    acc = s.rolling(scale, min_periods=scale).sum()
    out = pd.Series(np.nan, index=acc.index)
    calib = acc[(acc.index.year >= cal[0]) & (acc.index.year <= cal[1])]
    for mo in range(1, 13):
        ref = calib[calib.index.month == mo].dropna()
        nz = ref[ref > 0]
        if len(nz) < 10: continue
        if method == "mle":
            a, _, sc = stats.gamma.fit(nz.values, floc=0)
        else:
            a, sc = thom_fit(nz.values)
        q = (ref == 0).mean()
        tgt = acc[acc.index.month == mo].dropna()
        h = q + (1 - q) * stats.gamma.cdf(tgt.values, a, loc=0, scale=sc)
        out.loc[tgt.index] = stats.norm.ppf(np.clip(h, 1e-6, 1 - 1e-6))
    return out

AT = pd.Timestamp("2026-08-01")
def cls(v):
    r = round(v + 0.0, 2)
    for lim, c in ((-2.0, "D4"), (-1.6, "D3"), (-1.3, "D2"), (-0.8, "D1"), (-0.5, "D0")):
        if r <= lim: return c
    return "None"

# 1. SSI-3 (modelled runoff) on the scope's monthly depth series
nat = parse(section(os.path.join(S, "drought_sql_out.txt"), "J.")[0])
print("national depth months:", len(nat), nat.index.min().date(), nat.index.max().date())
for method in ("mle", "thom"):
    print(f"SSI-3 national (modelled runoff, {method}):", round(float(ssi(nat, 3, method=method).get(AT)), 2), " API 0.49")
for row in section(os.path.join(S, "drought2_out.txt"), "J2."):
    code, s = row.split(" | ")
    ser = parse(s)
    print(f"SSI-3 {code}: mle {ssi(ser, 3).get(AT):.2f}  thom {ssi(ser, 3, method='thom').get(AT):.2f}   API CPT -3.6 / DC34 0.98 / DC44 1.04")

# 2. Cascade streamflow: SSI-3 of each district's 9_1 monthly mean flow, area-weighted; severity = share of area at D1+
vals = {}
for row in section(os.path.join(S, "drought3_out.txt"), "J3."):
    code, s = row.split(" | ")
    ser = parse(s)
    v = ssi(ser, 3).get(AT)
    if v is not None and not np.isnan(v):
        vals[code] = float(v)
print("districts with SSI-3 of flow:", len(vals))
w = {k: areas[k] for k in vals}
nat_ssi = sum(vals[k] * w[k] for k in vals) / sum(w.values())
dry = sum(w[k] for k in vals if cls(vals[k]) in ("D1", "D2", "D3", "D4")) / sum(w.values()) * 100
print(f"cascade streamflow SSI-3 national {nat_ssi:.2f} (API 0.66), severity {dry:.0f} % (API 16), coverage {sum(w.values())/TOTAL*100:.1f} %")
for code in ("CPT", "DC34", "DC44"):
    print(f"  {code}: {vals.get(code):.2f}   API CPT -1.07 / DC34 3.58 / DC44 -0.01")
