"""Validate a district-boundary GeoJSON before the API uses it for area weights and WMA overlap.

Checks: 52 features that resolve to the 52 districts; valid geometry; each district lies in its province; districts do
not overlap; together they tile the provinces; areas against official figures for a few known districts.

Usage: python scripts/check_district_boundaries.py <file.geojson>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from shapely import make_valid
from shapely.geometry import shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.core import districts as dmod  # noqa: E402
from app.core.spatial import area_km2, get_spatial  # noqa: E402

# Official areas (km², Stats SA / MDB) for spot checks
KNOWN = {"CPT": 2_446, "JHB": 1_645, "ETH": 2_556, "TSH": 6_298, "DC6": 126_836, "DC8": 102_484, "DC5": 38_854}


def main(path: str) -> int:
    feats = json.loads(Path(path).read_text(encoding="utf-8"))["features"]
    print(f"features: {len(feats)}")
    geoms, problems = {}, []
    for f in feats:
        name = f["properties"].get("district_name")
        d = dmod.resolve(name or "")
        if d is None:
            problems.append(f"unmatched name {name!r}")
            continue
        g = shape(f["geometry"])
        if not g.is_valid:
            problems.append(f"{d.code} {name}: invalid geometry (repaired for checks)")
            g = make_valid(g)
        if d.code in geoms:
            problems.append(f"{d.code} {name}: duplicate feature")
        geoms[d.code] = g
    missing = sorted({d.code for d in dmod.districts()} - set(geoms))
    print(f"matched districts: {len(geoms)}; missing: {missing or 'none'}")

    prov = {r.id: r for r in get_spatial().provinces}
    print("\nprovince   sum(districts) km²   province polygon km²   diff %   districts outside province")
    for p, region in sorted(prov.items()):
        codes = [d.code for d in dmod.in_province(p) if d.code in geoms]
        s = sum(area_km2(geoms[c]) for c in codes)
        outside = [c for c in codes if not region.geom.buffer(0.05).contains(geoms[c].representative_point())]
        print(
            f"{p:5s} {s:18,.0f} {region.area_km2:20,.0f} {100 * (s - region.area_km2) / region.area_km2:8.2f}   {outside or ''}"
        )

    union = unary_union(list(geoms.values()))
    total = sum(area_km2(g) for g in geoms.values())
    overlap = total - area_km2(union)
    sa = unary_union([r.geom for r in prov.values()])
    print(
        f"\nsum of district areas {total:,.0f} km²; union {area_km2(union):,.0f}; overlap between districts {overlap:,.1f} km²"
    )
    print(
        f"provinces union {area_km2(sa):,.0f} km²; districts not covering provinces: {area_km2(sa.difference(union)):,.0f} km²; "
        f"districts outside provinces: {area_km2(union.difference(sa)):,.0f} km²"
    )

    print("\nknown areas:")
    for code, official in KNOWN.items():
        a = area_km2(geoms[code])
        print(
            f"  {code:5s} {dmod.by_code(code).official_name:22s} computed {a:9,.0f}  official {official:9,}  diff {100 * (a - official) / official:+.1f} %"
        )
    if problems:
        print("\nproblems:\n  " + "\n  ".join(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
