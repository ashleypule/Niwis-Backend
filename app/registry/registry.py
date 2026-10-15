"""Loads and validates app/registry/datasets.yaml.

Rules enforced at startup (the app refuses to start on a broken registry):
- every column references a variable in the catalogue;
- each variable has at most one `canonical` column; `duplicate` columns need a canonical twin;
- `withheld` columns carry a reason;
- every identifier is safe to quote (see app.db.ident).
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db import ident, qualified

REGISTRY_PATH = Path(__file__).with_name("datasets.yaml")

Role = Literal["canonical", "duplicate", "withheld", "unused"]
Repeats = Literal["monthly", "annual", "record"]
TimeKind = Literal["date", "text_date", "year_month", "year"]
Resolution = Literal["daily", "dekadal", "monthly", "annual"]
ModuleId = Literal["rainfall", "drought", "climate", "vegetation", "runoff"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Variable(_Strict):
    label: str
    unit: str
    metric: str | None = None


class Column(_Strict):
    var: str
    role: Role
    repeats: Repeats | None = None
    reason: str | None = None
    note: str | None = None
    cast: Literal["double"] | None = None
    transform: Literal["model_volume_to_depth_mm"] | None = None
    use: Literal["ratio_only"] | None = None
    # why the raw column is withheld from the database view although the API serves a value derived from it
    raw_reason: str | None = None

    @model_validator(mode="after")
    def _withheld_needs_reason(self) -> Column:
        if self.role == "withheld" and not self.reason:
            raise ValueError("withheld columns need a reason")
        if (self.transform or self.use) and not self.raw_reason:
            raise ValueError("columns served only after a transform need a raw_reason for the database view")
        return self


class Location(_Strict):
    column: str


class Time(_Strict):
    kind: TimeKind
    resolution: Resolution
    column: str | None = None
    year: str | None = None
    month: str | None = None

    @model_validator(mode="after")
    def _columns_for_kind(self) -> Time:
        need = {"date": ["column"], "text_date": ["column"], "year_month": ["year", "month"], "year": ["column"]}
        missing = [f for f in need[self.kind] if getattr(self, f) is None]
        if missing:
            raise ValueError(f"time kind {self.kind!r} needs {missing}")
        return self


class Dataset(_Strict):
    table: str
    schema_: str = Field("public", alias="schema")
    db_module: int
    module: ModuleId  # DB module that owns the table
    serves: list[ModuleId] = []  # contract modules whose endpoints read it (defaults to [module])
    location: Location
    time: Time
    columns: dict[str, Column]
    known_issues: list[str] = []
    model_area_km2: float | None = None
    spec: str | None = None  # spec section (e.g. "3.1"); defaults to the table name's number prefix (4_1 → 4.1)
    category_modules: list[ModuleId] = []  # module pages that show this table in the database view ([module])

    @property
    def fq(self) -> str:
        """Schema-qualified, quoted table name."""
        return qualified(self.schema_, self.table)

    def date_sql(self) -> str:
        """SQL expression giving the row's date (first day of the period for monthly/annual tables)."""
        t = self.time
        if t.kind == "date":
            assert t.column
            return f"{ident(t.column)}::date"
        if t.kind == "text_date":
            assert t.column
            return f"{ident(t.column)}::date"  # ISO yyyy-mm-dd text (verified for every row)
        if t.kind == "year_month":
            assert t.year and t.month
            return f"make_date({ident(t.year)}::int, {ident(t.month)}::int, 1)"
        assert t.column
        return f"make_date({ident(t.column)}::int, 1, 1)"

    def range_sql(self, lo: str = "$1", hi: str = "$2") -> str:
        """Index-friendly `date BETWEEN lo AND hi` predicate.

        For ISO text dates the raw column is compared with ISO text bounds (text order = date order), so an index on
        the column can be used; casting the column to date would prevent that. Bind the bounds with `range_args()`.
        """
        if self.time.kind == "text_date":
            assert self.time.column
            return f"{ident(self.time.column)} BETWEEN {lo}::text AND {hi}::text"
        return f"{self.date_sql()} BETWEEN {lo} AND {hi}"

    def range_args(self, start: Any, end: Any) -> tuple[Any, Any]:
        if self.time.kind == "text_date":
            return start.isoformat(), end.isoformat()
        return start, end

    def period_end_sql(self) -> str:
        """SQL expression for the last day the row covers (for asOf on monthly/annual data)."""
        res = self.time.resolution
        d = self.date_sql()
        if res == "monthly":
            return f"({d} + interval '1 month' - interval '1 day')::date"
        if res == "annual":
            return f"({d} + interval '1 year' - interval '1 day')::date"
        return d

    def column_sql(self, column: str) -> str:
        col = self.columns[column]
        expr = ident(column)
        if col.cast == "double":
            expr = f"NULLIF({expr}, '')::double precision"
        if col.transform == "model_volume_to_depth_mm":
            if self.model_area_km2 is None:
                raise ValueError(f"{self.table}: transform needs model_area_km2")
            # m³ / (km² × 10⁶ m²/km²) × 1000 mm/m
            expr = f"({expr} / {float(self.model_area_km2) * 1e6!r} * 1000.0)"
        return expr

    def served_columns(self) -> dict[str, Column]:
        return {name: c for name, c in self.columns.items() if c.role == "canonical"}


class Baseline(_Strict):
    stored_anomalies: str
    computed_period: tuple[int, int]
    note: str


class Registry(_Strict):
    version: int
    baseline: Baseline
    variables: dict[str, Variable]
    datasets: dict[str, Dataset]

    @model_validator(mode="before")
    @classmethod
    def _inject_table_names(cls, data: dict) -> dict:  # type: ignore[type-arg]
        for name, ds in (data.get("datasets") or {}).items():
            if isinstance(ds, dict):
                ds.setdefault("table", name)
                ds.setdefault("serves", [ds.get("module")])
                ds.setdefault("category_modules", [ds.get("module")])
        return data

    @model_validator(mode="after")
    def _consistency(self) -> Registry:
        errors: list[str] = []
        canonical: dict[str, list[str]] = defaultdict(list)
        dup: dict[str, list[str]] = defaultdict(list)
        for ds in self.datasets.values():
            for ident_name in [
                ds.table,
                ds.schema_,
                ds.location.column,
                *ds.columns,
                *(x for x in (ds.time.column, ds.time.year, ds.time.month) if x),
            ]:
                try:
                    ident(ident_name)
                except ValueError as exc:
                    errors.append(f"{ds.table}: {exc}")
            for col_name, col in ds.columns.items():
                if col.var not in self.variables:
                    errors.append(f"{ds.table}.{col_name}: unknown variable {col.var!r}")
                if col.role == "canonical":
                    canonical[col.var].append(f"{ds.table}.{col_name}")
                elif col.role == "duplicate":
                    dup[col.var].append(f"{ds.table}.{col_name}")
        for var, where in canonical.items():
            if len(where) > 1:
                errors.append(f"variable {var!r} has {len(where)} canonical sources: {where}")
        for var, where in dup.items():
            if var not in canonical:
                errors.append(f"variable {var!r} has duplicates {where} but no canonical source")
        if errors:
            raise ValueError("invalid dataset registry:\n  " + "\n  ".join(errors))
        return self

    # ---------------------------------------------------------------- lookups

    def canonical_source(self, var: str) -> tuple[Dataset, str] | None:
        """(dataset, column) serving `var`, or None when the variable is unavailable (withheld / not in DB)."""
        for ds in self.datasets.values():
            for col_name, col in ds.columns.items():
                if col.var == var and col.role == "canonical":
                    return ds, col_name
        return None

    def withheld_reasons(self, var: str) -> list[str]:
        return [
            f"{ds.table}.{name}: {c.reason}"
            for ds in self.datasets.values()
            for name, c in ds.columns.items()
            if c.var == var and c.role == "withheld"
        ]

    def duplicates(self) -> dict[str, dict[str, list[str]]]:
        """variable → {"canonical": [...], "duplicates": [...]} for variables stored in more than one place."""
        out: dict[str, dict[str, list[str]]] = {}
        for var in self.variables:
            where: dict[str, list[str]] = {"canonical": [], "duplicates": []}
            for ds in self.datasets.values():
                for name, c in ds.columns.items():
                    if c.var == var and c.role == "canonical":
                        where["canonical"].append(f"{ds.table}.{name}")
                    elif c.var == var and c.role == "duplicate":
                        where["duplicates"].append(f"{ds.table}.{name}")
            if where["duplicates"]:
                out[var] = where
        return out

    def datasets_for_module(self, module: str) -> list[Dataset]:
        """Tables a contract module's endpoints read (via `serves`)."""
        return [d for d in self.datasets.values() if module in d.serves]

    @property
    def modules(self) -> list[str]:
        return sorted({m for d in self.datasets.values() for m in d.serves})


def load_registry(path: Path = REGISTRY_PATH) -> Registry:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Registry.model_validate(raw)


@lru_cache
def get_registry() -> Registry:
    return load_registry()
