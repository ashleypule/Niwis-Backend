"""The 213 local municipalities and their parent districts (`app/reference/local_municipalities.csv`), for the
tables keyed by local municipality (`Region` in 18.x / 19.x; DECISIONS D46).

The file comes from the frontend's `lm-to-district.json` (each local municipality's centroid in the district
boundaries) plus the spellings the tables use (`aliases`). `district_of(name)` maps a `Region` value to a district
code: a local municipality, a metro or a district by any spelling (case, accents, trailing spaces and the words
"Metropolitan / Local Municipality" ignored); unknown names (e.g. "National", a stray HTML row) map to None.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core import districts

PATH = Path(__file__).resolve().parent.parent / "reference" / "local_municipalities.csv"


@dataclass(frozen=True)
class LocalMunicipality:
    lm_id: str
    name: str
    district_code: str
    province: str
    aliases: tuple[str, ...]


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(metropolitan|local)\s+municipality\b", " ", s)
    s = re.sub(r"\bmunicipality of\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


@lru_cache
def municipalities() -> tuple[LocalMunicipality, ...]:
    with PATH.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = tuple(
        LocalMunicipality(
            lm_id=r["lm_id"],
            name=r["name"],
            district_code=r["district_code"],
            province=r["province"],
            aliases=tuple(a for a in r["aliases"].split(";") if a),
        )
        for r in rows
    )
    if len(out) != 213:
        raise ValueError("local_municipalities.csv must list the 213 local municipalities")
    return out


@lru_cache
def _index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for d in districts.districts():  # districts and metros by every spelling the district registry knows
        for key in (d.code, d.db_name, d.official_name, *d.aliases):
            idx[norm(key)] = d.code
    for m in municipalities():
        for key in (m.name, *m.aliases):
            idx.setdefault(norm(key), m.district_code)
    for key, code in list(idx.items()):  # "O.R. Tambo" / "OR Tambo": the same name without spaces
        idx.setdefault(key.replace(" ", ""), code)
    return idx


def district_of(name: str | None) -> str | None:
    """District code of a `Region` value (a local municipality, metro or district by any spelling), else None."""
    if not name or len(name) > 120:
        return None
    key = norm(name)
    return _index().get(key) or _index().get(key.replace(" ", ""))


def in_district(code: str) -> list[LocalMunicipality]:
    return [m for m in municipalities() if m.district_code == code]


@lru_cache
def _lm_index() -> dict[str, LocalMunicipality]:
    idx: dict[str, LocalMunicipality] = {}
    for m in municipalities():
        for key in (m.lm_id, m.name, *m.aliases):
            idx.setdefault(norm(key), m)
    for key, m in list(idx.items()):
        idx.setdefault(key.replace(" ", ""), m)
    return idx


def lm_of(name_or_id: str | None) -> LocalMunicipality | None:
    """The local municipality (or metro) a `Region` value or an LM id names; None for a district or "National"."""
    if not name_or_id or len(name_or_id) > 120:
        return None
    key = norm(name_or_id)
    return _lm_index().get(key) or _lm_index().get(key.replace(" ", ""))


def by_id(lm_id: str) -> LocalMunicipality | None:
    m = lm_of(lm_id)
    return m if m is not None and m.lm_id.lower() == lm_id.strip().lower() else None
