"""The 52 district / metro municipalities that key every NIWIS table, with MDB codes, provinces and name aliases.

Source: Municipal Demarcation Board 2016 codes (app/reference/districts.csv). The database uses some pre-2016 names
(Cacadu, Eden, Sisonke, Uthungulu) and two tables use lowercase / shortened names; `resolve()` maps all of them.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "reference" / "districts.csv"

PROVINCES: dict[str, str] = {
    "EC": "Eastern Cape",
    "FS": "Free State",
    "GP": "Gauteng",
    "KZN": "KwaZulu-Natal",
    "LP": "Limpopo",
    "MP": "Mpumalanga",
    "NC": "Northern Cape",
    "NW": "North West",
    "WC": "Western Cape",
}


@dataclass(frozen=True)
class District:
    code: str  # MDB code, the API id (e.g. "DC44", "CPT")
    db_name: str  # spelling used by most tables
    official_name: str
    province: str
    kind: str  # "district" | "metro"
    aliases: tuple[str, ...]


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


@lru_cache
def districts() -> tuple[District, ...]:
    with PATH.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = tuple(
        District(
            code=r["mdb_code"],
            db_name=r["db_name"],
            official_name=r["official_name"],
            province=r["province"],
            kind=r["kind"],
            aliases=tuple(a for a in r["aliases"].split(";") if a),
        )
        for r in rows
    )
    if len(out) != 52 or len({d.code for d in out}) != 52:
        raise ValueError("districts.csv must list 52 unique districts")
    return out


@lru_cache
def _index() -> dict[str, District]:
    idx: dict[str, District] = {}
    for d in districts():
        for key in (d.code, d.db_name, d.official_name, *d.aliases):
            idx[_norm(key)] = d
    return idx


def resolve(name_or_code: str) -> District | None:
    """Any DB spelling, alias, official name or MDB code (case-insensitive) → District."""
    return _index().get(_norm(name_or_code))


def by_code(code: str) -> District | None:
    d = resolve(code)
    return d if d is not None and d.code.lower() == code.strip().lower() else None


def in_province(province: str) -> list[District]:
    return [d for d in districts() if d.province == province]


def db_names() -> list[str]:
    """Every spelling that may appear in a table's district column (for `= ANY($1)` filters)."""
    names: set[str] = set()
    for d in districts():
        names.update({d.db_name, d.db_name.lower(), *d.aliases})
    return sorted(names)
