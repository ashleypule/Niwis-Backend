"""Independent recomputation of the Water Quality page: guideline compliance and the CCME WQI (CCME 2001) from the
12.x tables, written fresh for the audit (SQL extraction + a separate CCME implementation)."""
import json, math, os, re, sys
import psycopg
from dotenv import dotenv_values
sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"C:\Users\Baloyi PA\Desktop\Niwis Backend"
S = os.path.dirname(os.path.abspath(__file__))
url = dotenv_values(os.path.join(ROOT, ".env"))["SUPABASE_DB_URL"]
G = {p["id"]: p for p in json.load(open(os.path.join(ROOT, "app/core/thresholds.json"), encoding="utf-8"))["wq_guidelines"]["params"]}
COLS = {  # guideline id -> (table, column)
    "ph": ("12.2_chemical_water_quality_master", "pH"), "ec": ("12.1_physical_water_quality_master", "Electrical Conductivity (EC)"),
    "turbidity": ("12.1_physical_water_quality_master", "Turbidity"), "do": ("12.2_chemical_water_quality_master", "Dissolved Oxygen (DO)"),
    "bod": ("12.2_chemical_water_quality_master", "BOD5"), "cod": ("12.2_chemical_water_quality_master", "COD"),
    "nitrate": ("12.2_chemical_water_quality_master", "Nitrate (NO3-N)"), "ammonia": ("12.2_chemical_water_quality_master", "Ammonia (NH3-N)"),
    "phosphate": ("12.2_chemical_water_quality_master", "Orthophosphate (PO43-)"), "ecoli": ("12.3_biological_water_quality_master", "E. coli"),
    "chla": ("12.3_biological_water_quality_master", "Chlorophyll-a"), "tds": ("12.1_physical_water_quality_master", "Total Dissolved Solids (TDS)"),
    "sulphate": ("12.2_chemical_water_quality_master", "Sulphate"), "fluoride": ("12.2_chemical_water_quality_master", "Fluoride"),
}
areas, names = {}, {}
for m in re.finditer(r"\('([^']*)','([A-Z0-9]+)','([A-Z]+)',([0-9.]+)\)", open(os.path.join(S, "w.sql"), encoding="utf-8").read()):
    areas[m.group(2)] = float(m.group(4)); names[m.group(1).lower()] = m.group(2)

def passes(pid, v):
    g = G[pid]
    return not (("min" in g and v < g["min"]) or ("max" in g and v > g["max"]))

def excursion(pid, v):
    g = G[pid]
    if "max" in g and v > g["max"]: return v / g["max"] - 1
    if "min" in g and v < g["min"]: return g["min"] / v - 1 if v > 0 else 1e6
    return 0.0

def wqi(tests):
    params = {p for p, _ in tests}
    failed = [(p, v) for p, v in tests if not passes(p, v)]
    f1 = len({p for p, _ in failed}) / len(params) * 100
    f2 = len(failed) / len(tests) * 100
    nse = sum(excursion(p, v) for p, v in failed) / len(tests)
    f3 = nse / (0.01 * nse + 0.01)
    return max(0.0, 100 - math.sqrt(f1 ** 2 + f2 ** 2 + f3 ** 2) / 1.732)

data = {}  # (code, (y, m)) -> {pid: value}
with psycopg.connect(url, autocommit=True) as conn:
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    for pid, (table, col) in COLS.items():
        rows = conn.execute(f'SELECT district_name, year, month, "{col}" FROM "{table}" WHERE make_date(year, month, 1) BETWEEN \'2024-10-01\' AND \'2026-09-01\'').fetchall()
        for name, y, m, v in rows:
            code = names.get(name.lower())
            if code is None or v is None: continue
            data.setdefault((code, (y, m)), {})[pid] = float(v)
codes = sorted(areas)
LAST = (2026, 9)
# compliance in Sep 2026
tests = [(c, p, v) for (c, ym), d in data.items() if ym == LAST for p, v in d.items()]
print("tests", len(tests), "compliance %", round(sum(passes(p, v) for _, p, v in tests) / len(tests) * 100, 1))
exceed = {c for c, p, v in tests if not passes(p, v)}
print("exceeding districts", len(exceed), "compliant", 52 - len(exceed))
for p in list(COLS)[:8]:
    pt = [(c, v) for c, pp, v in tests if pp == p]
    print(f"  param-compliance {p}: {round(sum(passes(p, v) for _, v in pt) / len(pt) * 100)} %")
# WQI per district over the 12 months ending Sep 2026, and Sep 2025
def months(end, n=12):
    y, m = end; out = []
    for i in range(n):
        out.append((y, m)); m -= 1
        if m == 0: y, m = y - 1, 12
    return out
def wqi_at(code, end):
    t = [(p, v) for ym in months(end) for p, v in data.get((code, ym), {}).items()]
    return wqi(t) if t else None
cur = {c: wqi_at(c, LAST) for c in codes}
prev = {c: wqi_at(c, (2025, 9)) for c in codes}
nat = sum(areas[c] * cur[c] for c in codes) / sum(areas.values())
print("national WQI (area-weighted)", round(nat, 2), " API 91")
print("sample", {c: round(cur[c], 1) for c in ("CPT", "DC34", "DC44", "DC16", "DC40", "DC1")})
print("poor (<50)", sum(1 for v in cur.values() if v < 50), "very poor (<25)", sum(1 for v in cur.values() if v < 25), "excellent (>=90)", sum(1 for v in cur.values() if v >= 90))
ranked = sorted(codes, key=lambda c: cur[c])
print("poorest 3", [(c, round(cur[c]), round(cur[c] - prev[c], 1)) for c in ranked[:3]], "best 3", [(c, round(cur[c]), round(cur[c] - prev[c], 1)) for c in ranked[-3:][::-1]])
# national WQI series Oct 2025 .. Sep 2026 (area-weighted mean of district WQI)
ser = []
for ym in months(LAST)[::-1]:
    w = {c: wqi_at(c, ym) for c in codes}
    ser.append((ym, round(sum(areas[c] * w[c] for c in codes) / sum(areas.values()), 1)))
print("WQI series", ser)
# latest national values Sep 2026 (area-weighted), EC Jan 2026 / Jan 2025, hotspots (worst ratio), param range
def nat_value(pid, ym):
    vals = {c: data[(c, ym)][pid] for c in codes if (c, ym) in data and pid in data[(c, ym)]}
    return sum(areas[c] * v for c, v in vals.items()) / sum(areas[c] for c in vals)
print("latest Sep 2026", {p: round(nat_value(p, LAST), 3) for p in ("ph", "ec", "turbidity", "do", "bod", "cod", "nitrate", "phosphate", "ecoli")})
print("EC Jan 2026", round(nat_value("ec", (2026, 1)), 3), "Jan 2025", round(nat_value("ec", (2025, 1)), 3), "Dec 2025", round(nat_value("ec", (2025, 12)), 3))
print("nitrate Oct 2025", round(nat_value("nitrate", (2025, 10)), 3), "ecoli Sep 2026", round(nat_value("ecoli", LAST), 3))
def ratio(pid, v):
    g = G[pid]
    return v / g["max"] if g.get("max") else g["min"] / v
bad = sorted([(ratio(p, v), c, p, v) for c, p, v in tests if not passes(p, v)], reverse=True)
print("hotspots", [(c, p, v, round(r, 1)) for r, c, p, v in bad[:4]])
for p in ("ec", "turbidity", "nitrate", "phosphate", "ammonia", "cod", "ecoli"):
    vs = [v for c, pp, v in tests if pp == p]
    print(f"  range {p}: min {round(min(vs), 3)} avg {round(sum(vs) / len(vs), 3)} max {round(max(vs), 3)}")
print("ratio maps BUF", {p: round(ratio(p, data[("BUF", LAST)][p]), 2) for p in ("nitrate", "phosphate", "do", "ph", "ecoli")}, "CPT phosphate", round(ratio("phosphate", data[("CPT", LAST)]["phosphate"]), 2))
print("DC44 entity", {p: data[("DC44", LAST)][p] for p in ("ec", "ph", "turbidity", "do", "nitrate", "phosphate", "ecoli")})
for c in ("CPT", "DC34", "DC44"):
    t = [(p, v) for p, v in data[(c, LAST)].items()]
    print(c, "compliance", round(sum(passes(p, v) for p, v in t) / len(t) * 100), "%", "fails", [p for p, v in t if not passes(p, v)])
