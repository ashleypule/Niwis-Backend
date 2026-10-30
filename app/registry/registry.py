"""Loads and validates app/registry/datasets.yaml.

Rules enforced at startup (the app refuses to start on a broken registry):
- every column references a variable in the catalogue;
- each variable has at most one `canonical` column; `duplicate` columns need a canonical twin;
- `withheld` columns carry a reason;
- every identifier is safe to quote (see app.db.ident).
"""

from __future__ import annotations

import datetime as dt
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
TimeKind = Literal["date", "text_date", "year_month", "year", "month_name_year"]  # "April 1994" → 1994-04-01
LocationLevel = Literal["district", "local_municipality"]
Resolution = Literal["daily", "dekadal", "monthly", "annual"]
ModuleId = Literal[
    "rainfall",
    "drought",
    "climate",
    "vegetation",
    "runoff",
    "dams",
    "surface-water",
    "groundwater",
    "groundwater-reserve",
    "water-quality",
    "green-drop",
    "blue-drop",
    "rwqo",
    "iwrm",
    "water-quantity",
    "services",
    "tariffs",
]
NATIONAL_CODE = "ZA"  # location code under which a national table's rows are served


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
    agg: Literal["sum", "mean"] | None = None  # local-municipality tables: how rows combine into their district
    reason: str | None = None
    note: str | None = None
    # "double": numbers stored as text; "bool_pct": a yes / no flag served as % (100 = yes; a scope value is the share
    # of the area where it is yes)
    cast: Literal["double", "bool_pct"] | None = None
    transform: Literal["model_volume_to_depth_mm"] | None = None
    use: Literal["ratio_only"] | None = None
    # why the raw column is withheld from the database view although the API serves a value derived from it
    raw_reason: str | None = None
    # first / last period with real values (start dates of the periods, inclusive): rows outside (a fill, a spin-up,
    # repeated values) are never read; `valid_reason` says why
    valid_from: dt.date | None = None
    valid_until: dt.date | None = None
    valid_reason: str | None = None
    wide: bool = False  # long-format table: this is a real column, not a value of the variable-name column
    # physically possible values [min, max] (either may be null): values outside are impossible and never served
    # (e.g. one negative SASS5 score in 16 744 rows); `range_reason` says why
    valid_range: tuple[float | None, float | None] | None = None
    range_reason: str | None = None

    @model_validator(mode="after")
    def _withheld_needs_reason(self) -> Column:
        if self.role == "withheld" and not self.reason:
            raise ValueError("withheld columns need a reason")
        if (self.transform or self.use) and not self.raw_reason:
            raise ValueError("columns served only after a transform need a raw_reason for the database view")
        if (self.valid_from or self.valid_until) and not self.valid_reason:
            raise ValueError("valid_from / valid_until need a valid_reason")
        if self.valid_range is not None and not self.range_reason:
            raise ValueError("valid_range needs a range_reason")
        return self


class Location(_Strict):
    """The column holding the district name; `null` for a national table (one row per period, no district
    breakdown: 17_2), whose rows are read as the single location "ZA". `level: local_municipality` (18.x / 19.x
    `Region`): rows are local municipalities, mapped to their district (app.core.municipalities) and aggregated per
    district by each column's `agg` (sum for counts and volumes, mean for rates and prices)."""

    column: str | None = None
    level: LocationLevel = "district"


class LongFormat(_Strict):
    """A long-format table (one row per variable): `name` holds the variable name, `value` its value, `unit` its unit.
    Registry columns of such a table are keyed by the variable name and read as
    `CASE WHEN name = '<variable>' THEN value END`, so every loader and aggregate works unchanged."""

    name: str
    value: str
    unit: str | None = None


class Time(_Strict):
    kind: TimeKind
    resolution: Resolution
    column: str | None = None
    year: str | None = None
    month: str | None = None

    @model_validator(mode="after")
    def _columns_for_kind(self) -> Time:
        need = {
            "date": ["column"],
            "text_date": ["column"],
            "year_month": ["year", "month"],
            "year": ["column"],
            "month_name_year": ["column"],
        }
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
    long: LongFormat | None = None  # long-format table: columns are the values of `long.name`
    # columns that are neither keys nor variables (labels, sources, geometry): not shown as variables
    key_columns: list[str] = []
    # monthly / annual values repeated on daily rows are present on the first day of every period (the category view
    # then reads only those days); false when they are stamped on another day (10_1: the 15th, 31 Dec)
    repeats_on_first_day: bool = True
    duplicate_rows: bool = False  # every period stored more than once (identical rows): loaders read one per period
    # location spellings whose rows are never read (8_1 holds "Siyanda" next to "Z F Mgcawu", identical copies)
    exclude_locations: list[str] = []
    # an extra SQL condition on every read (18_1: only the "Total" category rows; the Urban / Rural rows are read by
    # the module that wants them)
    row_filter: str | None = None

    @property
    def fq(self) -> str:
        """Schema-qualified, quoted table name."""
        return qualified(self.schema_, self.table)

    @property
    def national(self) -> bool:
        """A table without a district column: every row is a national value."""
        return self.location.column is None

    @property
    def sub_district(self) -> bool:
        """Rows are local municipalities, aggregated to districts when read."""
        return self.location.level == "local_municipality"

    def location_sql(self) -> str:
        """SQL expression giving the row's location name: the district column, or the constant "ZA" for a national
        table (so every reader's `location = ANY(names)` filter and grouping work unchanged)."""
        return ident(self.location.column) if self.location.column else f"'{NATIONAL_CODE}'"

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
        if t.kind == "month_name_year":
            assert t.column
            return f"to_date({ident(t.column)}, 'FMMonth YYYY')"  # "April 1994" (NULL text → NULL: never read)
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

    def not_future_sql(self) -> str:
        """SQL condition dropping rows dated after today (the database holds rows up to 2026-12-31 in some tables;
        they are never served). Text dates compare as ISO text so an index on the column stays usable."""
        if self.time.kind == "text_date":
            assert self.time.column
            return f"{ident(self.time.column)} <= to_char(CURRENT_DATE, 'YYYY-MM-DD')"
        return f"{self.date_sql()} <= CURRENT_DATE"

    def rows_sql(self) -> str:
        """SQL condition for the rows the API may read: never after today (`not_future_sql`), never an excluded
        location spelling (`exclude_locations`). Part of every column filter (`valid_sql`)."""
        cond = self.not_future_sql()
        if self.exclude_locations and self.location.column:
            names = ", ".join("'" + n.replace("'", "''") + "'" for n in self.exclude_locations)
            cond += f" AND {ident(self.location.column)} NOT IN ({names})"
        if self.row_filter:
            cond += f" AND ({self.row_filter})"
        return cond

    def raw_sql(self, column: str) -> str:
        """The stored value of a column: the column itself, or for a long-format table the value of that variable's
        rows (NULL on every other row, which every aggregate ignores)."""
        if self.long is not None and column not in self.wide_columns():
            name = column.replace("'", "''")  # registry names cannot hold quotes (ident); escaped regardless
            return f"(CASE WHEN {ident(self.long.name)} = '{name}' THEN {ident(self.long.value)} END)"
        return ident(column)

    def in_range(self, column: str, expr: str) -> str:
        """`expr`, or NULL where the value lies outside the column's `valid_range` (impossible values)."""
        col = self.columns.get(column)
        if col is None or col.valid_range is None:
            return expr
        lo, hi = col.valid_range
        conds = [f"{expr} >= {float(lo)!r}"] if lo is not None else []
        conds += [f"{expr} <= {float(hi)!r}"] if hi is not None else []
        return f"(CASE WHEN {' AND '.join(conds)} THEN {expr} END)" if conds else expr

    def wide_columns(self) -> set[str]:
        """Registry columns of a long-format table that are real columns (11_1 has both): marked by `wide: true`."""
        return {n for n, c in self.columns.items() if c.wide}

    def column_sql(self, column: str) -> str:
        col = self.columns[column]
        expr = self.raw_sql(column)
        if col.cast == "double":
            expr = f"NULLIF({expr}, '')::double precision"
        elif col.cast == "bool_pct":
            expr = f"(CASE WHEN {expr} THEN 100.0 WHEN NOT {expr} THEN 0.0 END)"
        expr = self.in_range(column, expr)
        if col.transform == "model_volume_to_depth_mm":
            if self.model_area_km2 is None:
                raise ValueError(f"{self.table}: transform needs model_area_km2")
            # m³ / (km² × 10⁶ m²/km²) × 1000 mm/m
            expr = f"({expr} / {float(self.model_area_km2) * 1e6!r} * 1000.0)"
        return expr

    def valid_sql(self, column: str) -> str:
        """SQL condition keeping only the column's valid periods (`valid_from` / `valid_until`) and never a row dated
        after today."""
        col = self.columns.get(column)
        parts = [self.rows_sql()]
        if col is None or (col.valid_from is None and col.valid_until is None):
            return parts[0]
        if col.valid_from:
            parts.append(f"{self.date_sql()} >= DATE '{col.valid_from.isoformat()}'")
        if col.valid_until:
            parts.append(f"{self.date_sql()} <= DATE '{col.valid_until.isoformat()}'")
        return " AND ".join(parts)

    def served_columns(self) -> dict[str, Column]:
        return {name: c for name, c in self.columns.items() if c.role == "canonical"}


class Baseline(_Strict):
    stored_anomalies: str
    computed_period: tuple[int, int]
    note: str


class Registry(_Strict):
    version: int
    ignored_tables: dict[str, str] = {}  # database tables kept out of the catalogue entirely → why
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
                *([ds.location.column] if ds.location.column else []),
                *ds.columns,
                *(x for x in (ds.time.column, ds.time.year, ds.time.month) if x),
                *ds.key_columns,
                *ds.exclude_locations,
                *((ds.long.name, ds.long.value, *([ds.long.unit] if ds.long.unit else [])) if ds.long else ()),
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
