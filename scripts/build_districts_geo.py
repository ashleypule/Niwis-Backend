"""Build reference/geo/districts.json from the full-resolution district boundaries.

Input: geoBoundaries ZAF ADM2 (52 district / metro municipalities), e.g. docs/south_africa_52_areas_clean.geojson
(32 MB). Output: a topology-preserving simplified copy (shared borders stay shared, no gaps or slivers) with the MDB code
as `id`, plus official name, database name, province and area. Validated with scripts/check_district_boundaries.py.

Usage: python scripts/build_districts_geo.py <input.geojson> [tolerance_degrees=0.002]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import shapely
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.core import districts as dmod  # noqa: E402
from app.core.spatial import area_km2  # noqa: E402

OUT = ROOT / "reference" / "geo" / "districts.json"


def _round(obj: object, nd: int = 5) -> object:
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, list | tuple):
        return [_round(x, nd) for x in obj]
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    return obj


def main(src: str, tolerance: float = 0.002) -> None:
    feats = json.loads(Path(src).read_text(encoding="utf-8"))["features"]
    codes, geoms, raw = [], [], {}
    for f in feats:
        d = dmod.resolve(f["properties"]["district_name"])
        if d is None:
            raise SystemExit(f"unmatched district {f['properties']['district_name']!r}")
        g = shape(f["geometry"])
        g = g if g.is_valid else shapely.make_valid(g)
        codes.append(d.code)
        geoms.append(g)
        raw[d.code] = (f["properties"], area_km2(g))
    if sorted(codes) != sorted(d.code for d in dmod.districts()):
        raise SystemExit("input must contain each of the 52 districts exactly once")

    simplified = shapely.coverage_simplify(geoms, tolerance=tolerance)  # keeps shared edges identical
    out_feats = []
    worst = 0.0
    for code, g in zip(codes, simplified, strict=True):
        d = dmod.by_code(code)
        assert d is not None
        props, full_area = raw[code]
        a = area_km2(g)
        worst = max(worst, abs(a - full_area) / full_area)
        out_feats.append(
            {
                "type": "Feature",
                "properties": {
                    "id": d.code,
                    "name": d.official_name,
                    "db_name": d.db_name,
                    "province": d.province,
                    "kind": d.kind,
                    "area_km2": round(full_area, 1),  # from the full-resolution polygon
                    "shapeID": props.get("shapeID"),
                },
                "geometry": _round(mapping(g)),
            }
        )
    out = {
        "type": "FeatureCollection",
        "name": "districts",
        "source": "geoBoundaries gbOpen ZAF ADM2 (via the supplied south_africa_52_areas_clean.geojson); "
        f"coverage-simplified, tolerance {tolerance}°",
        "features": sorted(out_feats, key=lambda f: f["properties"]["id"]),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.2f} MB); max area change {100 * worst:.3f} %")


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 0.002)
