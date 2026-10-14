"""Boundaries from reference/geo/ (copied from the frontend's public/geo) and area weights.

- Provinces (9, id = EC … WC) and WMAs (9, id = WMA1 … WMA9) are used as-is.
- District polygons (52): `municipalities.json` has 213 local municipalities but **no district code**, so districts
  cannot be dissolved from it. Official district boundaries are expected as `reference/geo/districts.json`
  (FeatureCollection, a property holding the MDB district code). Until that file exists, area weights and the
  district–WMA overlap are unavailable and every aggregate that needs them returns DATA_UNAVAILABLE.

Areas are computed on an equal-area projection (Albers, SA standard parallels −24° / −33°, WGS84 sphere of equal
area), which is accurate to well under 1 % at provincial scale — ample for weighting.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from app.core import districts as dmod

GEO_DIR = Path(__file__).resolve().parent.parent.parent / "reference" / "geo"
DISTRICT_FILE = "districts.json"
DISTRICT_CODE_PROPS = ("mdb_code", "district_code", "DISTRICT", "DC_MDB_C", "CAT_B", "code", "id", "shapeID")

# Albers equal-area conic, sphere with the WGS84 authalic radius
_R = 6_371_007.2
_LAT1, _LAT2, _LAT0, _LON0 = map(math.radians, (-24.0, -33.0, 0.0, 25.0))
_N = (math.sin(_LAT1) + math.sin(_LAT2)) / 2
_C = math.cos(_LAT1) ** 2 + 2 * _N * math.sin(_LAT1)
_RHO0 = _R * math.sqrt(_C - 2 * _N * math.sin(_LAT0)) / _N


def _albers(lonlat: Any) -> Any:
    """(n, 2) array of lon/lat degrees → (n, 2) array of Albers x/y metres."""
    lam, phi = np.radians(lonlat[:, 0]), np.radians(lonlat[:, 1])
    rho = _R * np.sqrt(_C - 2 * _N * np.sin(phi)) / _N
    theta = _N * (lam - _LON0)
    return np.column_stack([rho * np.sin(theta), _RHO0 - rho * np.cos(theta)])


def area_km2(geom: BaseGeometry) -> float:
    return float(shapely.transform(geom, _albers).area / 1e6)


@dataclass
class Region:
    id: str
    name: str
    geom: BaseGeometry
    area_km2: float
    props: dict[str, Any] = field(default_factory=dict)


def _load(name: str) -> list[dict[str, Any]]:
    path = GEO_DIR / name
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("type") != "FeatureCollection":
        raise ValueError(f"{name}: expected a GeoJSON FeatureCollection")
    feats: list[dict[str, Any]] = data["features"]
    return feats


def _regions(name: str) -> list[Region]:
    out = []
    for f in _load(name):
        p = f.get("properties") or {}
        g = shape(f["geometry"])
        out.append(Region(str(p["id"]), str(p.get("name", p["id"])), g, area_km2(g), p))
    return out


@dataclass
class Spatial:
    provinces: list[Region]
    wmas: list[Region]
    districts: list[Region]  # empty until reference/geo/districts.json is supplied
    district_problems: list[str]
    _overlap: dict[str, dict[str, float]] | None = None

    @property
    def has_districts(self) -> bool:
        return len(self.districts) == 52

    def district_areas(self) -> dict[str, float] | None:
        """MDB code → km², or None when district boundaries are not available."""
        return {r.id: r.area_km2 for r in self.districts} if self.has_districts else None

    def wma_overlap(self) -> dict[str, dict[str, float]] | None:
        """WMA id → {district code: overlapping area km²}. None without district boundaries."""
        if not self.has_districts:
            return None
        if self._overlap is None:
            ov: dict[str, dict[str, float]] = {}
            for w in self.wmas:
                ov[w.id] = {}
                for d in self.districts:
                    if w.geom.intersects(d.geom):
                        a = area_km2(w.geom.intersection(d.geom))
                        if a > 1.0:  # ignore slivers from simplified boundaries
                            ov[w.id][d.id] = a
            self._overlap = ov
        return self._overlap

    def wma_districts(self) -> dict[str, tuple[str, ...]] | None:
        ov = self.wma_overlap()
        return None if ov is None else {w: tuple(sorted(ds)) for w, ds in ov.items()}


def _load_districts() -> tuple[list[Region], list[str]]:
    feats = _load(DISTRICT_FILE)
    if not feats:
        return [], [f"reference/geo/{DISTRICT_FILE} not supplied (municipalities.json has no district code)"]
    problems: list[str] = []
    regions: dict[str, Region] = {}
    for f in feats:
        p = f.get("properties") or {}
        raw = next((str(p[k]) for k in DISTRICT_CODE_PROPS if p.get(k)), None)
        name = str(p.get("name") or p.get("NAME") or raw)
        d = (dmod.by_code(raw) if raw else None) or dmod.resolve(name)
        if d is None:
            problems.append(f"boundary feature {raw or name!r} does not match any of the 52 districts")
            continue
        g = shape(f["geometry"])
        if d.code in regions:  # multi-part districts delivered as several features
            prev = regions[d.code]
            g = prev.geom.union(g)
        regions[d.code] = Region(d.code, d.official_name, g, area_km2(g), p)
    missing = sorted({d.code for d in dmod.districts()} - set(regions))
    if missing:
        problems.append(f"districts without a boundary: {', '.join(missing)}")
    return list(regions.values()), problems


@lru_cache
def get_spatial() -> Spatial:
    dist, problems = _load_districts()
    return Spatial(_regions("provinces.json"), _regions("wmas.json"), dist, problems)
