"""Places the frontend's filters and search need: the 213 local municipalities (with their district) and a search
index over provinces, WMAs, districts and local municipalities. All from committed reference files — no database."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.core import districts
from app.core.spatial import get_spatial
from app.schemas.contract import RefItem, SearchHit

ROOT = Path(__file__).resolve().parent.parent.parent
LM_GEO = ROOT / "reference" / "geo" / "municipalities.json"
LM_TO_DISTRICT = ROOT / "app" / "reference" / "lm-to-district.json"


@lru_cache
def local_municipalities() -> tuple[RefItem, ...]:
    """Local / metropolitan municipalities (`id` LM001…, `parent` = MDB district code), sorted by name."""
    to_district: dict[str, str] = json.loads(LM_TO_DISTRICT.read_text(encoding="utf-8"))["lmToDistrict"]
    features = json.loads(LM_GEO.read_text(encoding="utf-8"))["features"]
    items = [
        RefItem(
            id=str(f["properties"]["id"]),
            name=str(f["properties"]["name"]),
            province=str(f["properties"]["province"]),
            parent=to_district.get(str(f["properties"]["id"])),
        )
        for f in features
    ]
    if len(items) != 213 or any(i.parent is None for i in items):
        raise ValueError("every local municipality needs a district in app/reference/lm-to-district.json")
    return tuple(sorted(items, key=lambda i: i.name))


@lru_cache
def search_index() -> tuple[SearchHit, ...]:
    provinces = districts.PROVINCES
    hits = [
        SearchHit(type="province", id=k, label=v, sublabel="Province", path="/", params={"province": k})
        for k, v in provinces.items()
    ]
    hits += [
        SearchHit(
            type="wma", id=r.id, label=f"{r.name} WMA", sublabel="Water Management Area", path="/", params={"wma": r.id}
        )
        for r in get_spatial().wmas
    ]
    hits += [
        SearchHit(
            type="district",
            id=d.code,
            label=d.official_name,
            sublabel=f"{'Metropolitan' if d.kind == 'metro' else 'District'} municipality, {provinces[d.province]}",
            path="/",
            params={"province": d.province, "district": d.code},
        )
        for d in districts.districts()
    ]
    hits += [
        SearchHit(
            type="municipality",
            id=m.id,
            label=m.name,
            sublabel=f"Local municipality, {provinces.get(m.province or '', m.province or '')}",
            path="/",
            params={"province": m.province or "", "district": m.parent or "", "municipality": m.id},
        )
        for m in local_municipalities()
    ]
    return tuple(hits)


def search(q: str, limit: int = 25) -> list[SearchHit]:
    """Exact label, then prefix, then substring, then id, then sublabel matches; at least two characters."""
    t = q.strip().lower()
    if len(t) < 2:
        return []

    def score(h: SearchHit) -> int:
        label = h.label.lower()
        if label == t:
            return 0
        if label.startswith(t):
            return 1
        if t in label:
            return 2
        if t in h.id.lower():
            return 3
        if t in (h.sublabel or "").lower():
            return 4
        return 9

    scored = sorted(((score(h), h.label, h) for h in search_index()), key=lambda x: (x[0], x[1]))
    return [h for s, _, h in scored if s < 9][:limit]
