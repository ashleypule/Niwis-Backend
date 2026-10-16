"""The database view: every registered table is a category, every measure column a variable.

`build_catalogue` turns the registry (app/registry/datasets.yaml: roles, reasons, time resolution) and the database
schema (`information_schema.columns`) into the catalogue that scripts/build_catalogue.py writes to
app/registry/catalogue.yaml; the API loads that file (`get_catalogue`). Rules (docs/CATEGORY_CATALOGUE.md):

- category = table, labelled from the table name (number prefix, `master` and underscores removed), with its spec
  section;
- variable = column; label and unit from the column name (app.core.units); key columns (district, date, year, month,
  day, id) are not variables;
- status: `live` (canonical or plausibility-checked unused columns), `withheld` (registry reason; also raw columns the
  API only serves transformed, and new columns nobody has reviewed yet) or `duplicate` (of the canonical column).
  A duplicate is hidden on a page only when its canonical column's table is on the same page
  (`category_modules`); elsewhere it is served as a live column of its own table ("same values as …"), so no page loses
  a variable because its canonical table lives on another page;
- chart groups (axis rule): one unit per group. Unitless standardised indices (SPI, SPEI, SSI, SGI, RAI, SVI) share an
  `index` group, spectral indices (NDVI, NDWI, NDII, EVI, SAVI, MSAVI2) another, other unitless columns are grouped by
  their name prefix. Within a unit, columns of the same resolution share a group; a name prefix shared by several of
  them (Dam inflow / outflow, LST day / night) splits off its own group. Group names come from the column names.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from app.core import units
from app.core.provenance import Resolution
from app.registry.registry import Column, Dataset, ModuleId, Registry
from app.registry.registry import Resolution as TableResolution

CATALOGUE_PATH = Path(__file__).with_name("catalogue.yaml")
Status = Literal["live", "withheld", "duplicate"]
Kind = Literal["numeric", "categorical"]
Chart = Literal["bar", "diverging-bar", "line"]
GroupChart = Literal["bar", "diverging-bar", "line", "band", "mixed"]
# recommended chart type (BACKEND_ISSUES #8): bars for totals and counts, diverging bars for anomalies, lines for states
# and indices, a band for min / max pairs
TOTAL_WORDS = {"rainfall", "rain", "precip", "precipitation", "prcptot", "map", "rx1day", "rx5day", "chirps", "tamsat"}
TOTAL_WORDS |= {"gridded", "total", "totals", "seasonal", "demand", "deficit", "return"}
VOLUME_UNITS = {"Mm³", "Mm³/month", "m³/day", "m³/year", "°C·days"}
AMOUNT_UNITS = {"mm", "mm/day", "mm/month", "mm/season", "mm/dekad"}
COUNT_UNITS = {"days", "days/month"}
BAND_PAIRS = (("tmin", "tmax"), ("min", "max"))
KEY_NAMES = {"id", "day", "date", "year", "month"} | {
    "district",
    "district_name",
    "district_id",
    "province",
    "province_name",
}
TEXT_TYPES = {"text", "character varying", "character"}
NEW_COLUMN = "New column, not reviewed yet: register it in app/registry/datasets.yaml after a plausibility check"
# column-name words → the family they belong to (the word itself, or the expansion of the acronym in the name)
FAMILY_WORDS = {
    "tmin": "Temperature",
    "tmax": "Temperature",
    "tavg": "Temperature",
    "temperature": "Temperature",
    "et0": "Evapotranspiration",
    "pet": "Evapotranspiration",
    "aet": "Evapotranspiration",
    "et": "Evapotranspiration",
    "evapotranspiration": "Evapotranspiration",
    "evap": "Evapotranspiration",
    "precip": "Precipitation",
    "precipitation": "Precipitation",
    "map": "Precipitation",
    "moisture": "Soil Moisture",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CatColumn(_Strict):
    column: str
    name: str  # label without the unit
    label: str  # label with the unit, e.g. "Daily Rainfall (mm/day)"
    unit: str | None
    db_type: str
    kind: Kind
    resolution: Resolution
    status: Status
    reason: str | None = None  # withheld: why
    duplicate_of: str | None = None  # duplicate: canonical table.column
    chart: Chart = "line"  # recommended chart type for this column
    note: str | None = None  # registry note (method, caveat)
    unit_override: str | None = None  # why the unit differs from the column-name suffix


class CatGroup(_Strict):
    id: str
    label: str
    unit: str  # axis unit: the columns' unit, "index" for unitless indices, "—" for other unitless, "class"
    resolution: Resolution
    columns: list[str]
    default_visible: list[str]
    chart: GroupChart = "line"  # the columns' common chart type, "band" for a min / max pair, else "mixed"
    band: list[str] | None = None  # [min column, max column] when chart is "band"


class CatPage(_Strict):
    """The table as one module page shows it: its chart groups there, and the duplicates served on that page."""

    module: ModuleId
    shown_duplicates: list[str]  # duplicates whose canonical table is not on this page: served as live columns
    groups: list[CatGroup]


class Category(_Strict):
    id: str
    table: str
    label: str
    spec: str | None
    modules: list[ModuleId]
    resolution: TableResolution
    keys: list[str]
    columns: list[CatColumn]
    pages: list[CatPage]

    def column(self, name: str) -> CatColumn | None:
        return next((c for c in self.columns if c.column == name), None)

    def page(self, module: str) -> CatPage:
        p = next((p for p in self.pages if p.module == module), None)
        if p is None:
            raise KeyError(f"{self.table} is not on the {module} page")
        return p

    def groups_for(self, module: str) -> list[CatGroup]:
        return self.page(module).groups

    def group(self, gid: str, module: str) -> CatGroup | None:
        return next((g for g in self.groups_for(module) if g.id == gid), None)

    def status_on(self, module: str, column: str) -> Status:
        """Status on a page: a duplicate whose canonical table is not on that page is live there."""
        x = self.column(column)
        assert x is not None
        return "live" if column in self.page(module).shown_duplicates else x.status

    def group_of(self, module: str, column: str) -> CatGroup | None:
        return next((g for g in self.groups_for(module) if column in g.columns), None)


class SchemaColumn(_Strict):
    column: str
    db_type: str


class Unregistered(_Strict):
    table: str
    columns: list[SchemaColumn]


class Catalogue(_Strict):
    generated: str
    source: str
    categories: list[Category]
    unregistered: list[Unregistered] = []

    def category(self, cid: str) -> Category | None:
        return next((c for c in self.categories if c.id == cid), None)

    def for_module(self, module: str) -> list[Category]:
        return [c for c in self.categories if module in c.modules]


# ---------------------------------------------------------------- naming


def slug(text: str) -> str:
    t = text.replace("%", "pct").replace("°C", "c").replace("³", "3").replace("²", "2").replace("₀", "0")
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def category_label(table: str) -> str:
    """`6.1_core_rainfall_variables_master` → "Core Rainfall Variables"; `meteorological_drought_master` →
    "Meteorological Drought"."""
    name = re.sub(r"[\s_]*master\s*$", "", table.strip(), flags=re.I).strip(" _")
    name = re.sub(r"^\d+[._]\d+_", "", name)
    return units.name_of(name)


def spec_of(ds: Dataset) -> str | None:
    if ds.spec:
        return ds.spec
    m = re.match(r"^(\d+)[._](\d+)_", ds.table)
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _tokens(column: str) -> list[str]:
    base, _ = units.split_unit(column)
    return [t.lower() for t in re.split(r"[_\s]+", base) if t]


def _family(column: str) -> str | None:
    return next((FAMILY_WORDS[t] for t in _tokens(column) if t in FAMILY_WORDS), None)


def _common_words(names: list[str]) -> str:
    split = [n.replace(" – ", " ").split(" ") for n in names]
    out = []
    for words in zip(*split, strict=False):
        if len(set(words)) != 1:
            break
        out.append(words[0])
    return " ".join(out)


def group_name(cols: list[CatColumn], family: str | None) -> str:
    """Group label from its columns' names: the shared index acronyms, the shared family, the shared leading words, or
    the names themselves — never new wording."""
    names = [c.name for c in cols]
    if family in ("standardised", "spectral"):
        acr = list(dict.fromkeys(n.split(" ", 1)[0] for n in names))
        return f"{' / '.join(acr)} (index)"
    unit = cols[0].unit
    if len(cols) == 1:
        base = names[0]
    else:
        fams = {_family(c.column) for c in cols}
        joined, common = " / ".join(names), _common_words(names)
        if len(fams) == 1 and None not in fams:
            base = str(next(iter(fams)))
        elif len(joined) <= 40 or not common:
            base = joined
        else:
            base = common
    if cols[0].kind == "categorical":
        return base if base.lower().endswith("class") else f"{base} (class)"
    return units.with_unit(base, unit)


def axis_unit(cols: list[CatColumn], family: str | None) -> str:
    if cols[0].kind == "categorical":
        return "class"
    if family in ("standardised", "spectral"):
        return "index"
    return cols[0].unit or "—"


# ---------------------------------------------------------------- builder


def chart_of(column: str, unit: str | None, resolution: Resolution) -> Chart:
    """Recommended chart type for a column, from its name, unit and resolution."""
    tokens = set(_tokens(column))
    if unit is None and units.index_family(units.name_of(units.split_unit(column)[0])):
        return "line"  # standardised / spectral indices (SPI, SPEI, RAI, NDVI …) are states
    if "anomaly" in tokens:
        return "diverging-bar"
    if unit in COUNT_UNITS or tokens & {"frequency", "number"}:
        return "bar"
    if unit in VOLUME_UNITS or (unit in AMOUNT_UNITS and (tokens & TOTAL_WORDS or resolution != "daily")):
        return "bar"  # totals: precipitation-type amounts, monthly / annual sums, volumes, degree-day sums
    return "line"


def _band(members: list[CatColumn]) -> list[str] | None:
    """[min column, max column] when the group holds a min / max pair (Tmin / Tmax)."""
    by_token = {t: c.column for c in members for t in _tokens(c.column)}
    for lo, hi in BAND_PAIRS:
        if lo in by_token and hi in by_token:
            return [by_token[lo], by_token[hi]]
    return None


def _resolution(ds: Dataset, column: str | None) -> Resolution:
    rep = ds.columns[column].repeats if column and column in ds.columns else None
    if rep == "monthly":
        return "monthly"
    if rep in ("annual", "record"):
        return "annual"
    res = ds.time.resolution
    if res == "dekadal":
        raise ValueError(f"{ds.table}: dekadal tables are not supported by the database view yet")
    return res


def _status(reg: Registry, ds: Dataset, column: str) -> tuple[Status, str | None, str | None]:
    """(status, withheld reason, duplicate-of) from the registry role."""
    col = ds.columns.get(column)
    if col is None:
        return "withheld", NEW_COLUMN, None
    if col.role == "withheld":
        return "withheld", col.reason, None
    if col.role == "duplicate":
        src = reg.canonical_source(col.var)
        assert src is not None  # the registry validator guarantees a canonical twin
        return "duplicate", None, f"{src[0].table}.{src[1]}"
    if col.raw_reason:
        return "withheld", col.raw_reason, None
    return "live", None, None


def _note(col: Column | None) -> str | None:
    """Registry note, plus the first served period when earlier rows are a fill (`valid_from`)."""
    if col is None:
        return None
    parts = [col.note] if col.note else []
    if col.valid_from:
        parts.append(f"Served from {col.valid_from:%b %Y}: {col.valid_reason}")
    return "; ".join(parts) or None


def _columns(reg: Registry, ds: Dataset, schema: list[SchemaColumn]) -> tuple[list[str], list[CatColumn]]:
    keys_named = {x for x in (ds.location.column, ds.time.column, ds.time.year, ds.time.month) if x}
    keys, out = [], []
    for sc in schema:
        if sc.column in keys_named or sc.column.lower() in KEY_NAMES:
            keys.append(sc.column)
            continue
        name, unit, why = units.column_label(ds.table, sc.column)
        reg_col = ds.columns.get(sc.column)
        categorical = sc.db_type in TEXT_TYPES and not (reg_col and reg_col.cast)
        status, reason, dup = _status(reg, ds, sc.column)
        out.append(
            CatColumn(
                column=sc.column,
                name=name,
                label=units.with_unit(name, unit),
                unit=unit,
                db_type=sc.db_type,
                kind="categorical" if categorical else "numeric",
                resolution=_resolution(ds, sc.column),
                status=status,
                reason=reason,
                duplicate_of=dup,
                chart=chart_of(sc.column, unit, _resolution(ds, sc.column)) if not categorical else "line",
                note=_note(reg_col),
                unit_override=why,
            )
        )
    return keys, out


def _buckets(cols: list[CatColumn]) -> list[tuple[str | None, list[CatColumn]]]:
    """Columns that may share a chart: same unit (or index family / name prefix when unitless) and resolution, in table
    order; a name prefix shared by several columns of a unit splits off its own group."""
    buckets: dict[tuple[str, str], list[CatColumn]] = {}
    family: dict[tuple[str, str], str | None] = {}
    key: tuple[str, str]
    for c in cols:
        fam = units.index_family(c.name) if c.unit is None and c.kind == "numeric" else None
        if c.kind == "categorical":
            key = (f"class:{_tokens(c.column)[0]}", c.resolution)
        elif c.unit is not None:
            key = (f"unit:{c.unit}", c.resolution)
        elif fam is not None:
            key = (f"index:{fam}", c.resolution)
        else:
            key = (f"none:{_tokens(c.column)[0]}", c.resolution)
        buckets.setdefault(key, []).append(c)
        family[key] = fam
    out: list[tuple[str | None, list[CatColumn]]] = []
    for bkey, members in buckets.items():
        if family[bkey] is not None or len(members) < 3:
            out.append((family[bkey], members))
            continue
        by_prefix: dict[str, list[CatColumn]] = {}
        for c in members:
            by_prefix.setdefault(_tokens(c.column)[0], []).append(c)
        shared = [g for g in by_prefix.values() if len(g) >= 2]
        rest = [c for g in by_prefix.values() if len(g) < 2 for c in g]
        if not shared or (len(shared) == 1 and not rest):
            out.append((None, members))
            continue
        parts = [*shared, *([rest] if rest else [])]
        parts.sort(key=lambda g: members.index(g[0]))
        out += [(None, g) for g in parts]
    out.sort(key=lambda fg: cols.index(fg[1][0]))
    return out


def _groups(charted: list[CatColumn]) -> list[CatGroup]:
    """Chart groups for the columns a page charts (statuses as on that page)."""
    groups: list[CatGroup] = []
    for fam, members in _buckets(charted):
        label = group_name(members, fam)
        gid, n = slug(label), 2
        while any(g.id == gid for g in groups):
            gid, n = f"{slug(label)}-{n}", n + 1
        live = [c.column for c in members if c.status == "live"]
        default = (live or [c.column for c in members])[:3]
        band = _band(members)
        charts = {c.chart for c in members}
        chart: GroupChart = "band" if band else (members[0].chart if len(charts) == 1 else "mixed")
        groups.append(
            CatGroup(
                id=gid,
                label=label,
                unit=axis_unit(members, fam),
                resolution=members[0].resolution,
                columns=[c.column for c in members],
                default_visible=default,
                chart=chart,
                band=band,
            )
        )
    return groups


def build_category(
    reg: Registry, ds: Dataset, schema: list[SchemaColumn], page_tables: dict[str, set[str]]
) -> Category:
    keys, cols = _columns(reg, ds, schema)
    pages = []
    for module in ds.category_modules:
        shown = [
            c.column
            for c in cols
            if c.status == "duplicate" and str(c.duplicate_of).rpartition(".")[0] not in page_tables[module]
        ]
        charted = [
            c.model_copy(update={"status": "live"}) if c.column in shown else c
            for c in cols
            if c.status != "duplicate" or c.column in shown
        ]
        pages.append(CatPage(module=module, shown_duplicates=shown, groups=_groups(charted)))
    label = category_label(ds.table)
    return Category(
        id=slug(label),
        table=ds.table,
        label=label,
        spec=spec_of(ds),
        modules=list(ds.category_modules),
        resolution=ds.time.resolution,
        keys=keys,
        columns=cols,
        pages=pages,
    )


def build_catalogue(
    reg: Registry, schema: dict[str, list[SchemaColumn]], generated: str, source: str = "information_schema.columns"
) -> Catalogue:
    """Catalogue for every registered table, from the database schema. Fails when a registered table or column is
    missing from the database (the registry is out of date); tables the registry does not know are listed as
    `unregistered`."""
    missing = [ds.table for ds in reg.datasets.values() if ds.table not in schema]
    gone = [
        f"{ds.table}.{c}"
        for ds in reg.datasets.values()
        if ds.table in schema
        for c in ds.columns
        if c not in {s.column for s in schema[ds.table]}
    ]
    if missing or gone:
        raise ValueError(f"registry entries not in the database: {missing + gone}")
    page_tables: dict[str, set[str]] = {}
    for ds in reg.datasets.values():
        for m in ds.category_modules:
            page_tables.setdefault(m, set()).add(ds.table)
    cats = [build_category(reg, ds, schema[ds.table], page_tables) for ds in reg.datasets.values()]
    ids = [c.id for c in cats]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate category ids: {ids}")
    known = {ds.table for ds in reg.datasets.values()}
    unreg = [Unregistered(table=t, columns=cols) for t, cols in sorted(schema.items()) if t not in known]
    return Catalogue(generated=generated, source=source, categories=cats, unregistered=unreg)


# ---------------------------------------------------------------- files


def dump_yaml(cat: Catalogue) -> str:
    head = (
        "# GENERATED by scripts/build_catalogue.py from information_schema.columns and app/registry/datasets.yaml.\n"
        "# Do not edit by hand: change the registry (roles, reasons) or app/core/units.py, then re-run the script.\n"
    )
    body = yaml.safe_dump(
        cat.model_dump(mode="json", exclude_defaults=False), sort_keys=False, allow_unicode=True, width=200
    )
    return head + body


def schema_of(cat: Catalogue) -> dict[str, list[SchemaColumn]]:
    """The database schema the catalogue was built from (keys + columns of registered tables, unregistered tables)."""
    out: dict[str, list[SchemaColumn]] = {}
    for c in cat.categories:
        types = {x.column: x.db_type for x in c.columns}
        out[c.table] = [SchemaColumn(column=k, db_type="key") for k in c.keys] + [
            SchemaColumn(column=x.column, db_type=types[x.column]) for x in c.columns
        ]
    for u in cat.unregistered:
        out[u.table] = list(u.columns)
    return out


def load_catalogue(path: Path = CATALOGUE_PATH) -> Catalogue:
    with path.open(encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)
    return Catalogue.model_validate(raw)


@lru_cache
def get_catalogue() -> Catalogue:
    return load_catalogue()


def all_columns(cat: Catalogue) -> Iterable[tuple[str, str]]:
    for c in cat.categories:
        for k in c.keys:
            yield c.table, k
        for x in c.columns:
            yield c.table, x.column
