"""Response models mirroring the zod schemas in reference/API_CONTRACT.md (frontend src/types/api.ts).

Field names, nesting and optionality match the contract exactly. zod `.optional()` fields must be *absent*, never
`null`, so every response is serialised with `exclude_unset=True` (see `ContractModel.dump`) and services only set
optional fields they have values for. `Num` (number | null) fields are required and may be `null`.

Backwards-compatible additions (all optional; listed in docs/CONTRACT_CHANGES.md) are grouped in `Provenance` and in
the extra optional fields marked `# addition`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Num = float | None
XVal = str | float
Cell = str | float | None


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    def dump(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_unset=True, mode="json")


# ------------------------------------------------------------------ provenance additions


class UnavailableField(ContractModel):
    """A field returned as null because the database cannot provide it yet."""

    field: str
    reason: str


class Component(ContractModel):
    key: str
    as_of: str | None = Field(None, alias="asOf")
    source: list[str] = []
    is_stale: bool = Field(False, alias="isStale")
    period_end: str | None = Field(None, alias="periodEnd")


Aggregation = Literal["district", "district-area-weighted", "district-overlap"]


class Provenance(ContractModel):
    """Optional provenance fields carried by every top-level response (additions)."""

    source: list[str] | None = None
    is_stale: bool | None = Field(None, alias="isStale")
    period_end: str | None = Field(None, alias="periodEnd")  # last day of the asOf period (monthly / annual values)
    components: list[Component] | None = None
    baseline: str | None = None
    baseline_note: str | None = Field(None, alias="baselineNote")
    aggregation: Aggregation | None = None
    unavailable: list[UnavailableField] | None = None


# ------------------------------------------------------------------ scope / KPIs


class Scope(ContractModel):
    level: Literal["national", "province", "wma", "catchment", "municipality", "entity"]
    id: str
    name: str


class Status(ContractModel):
    scale: str
    id: str
    label: str


class Kpi(ContractModel):
    id: str
    metric: str
    label: str
    sublabel: str | None = None
    value: Num
    unit: str | None = None
    decimals: float | None = None
    status: Status | None = None
    text: str | None = None
    delta: Num = None
    delta_unit: str | None = Field(None, alias="deltaUnit")
    delta_label: str | None = Field(None, alias="deltaLabel")
    good_direction: Literal["up", "down", "neutral"] | None = Field(None, alias="goodDirection")
    spark: list[float] | None = None
    # additions
    as_of: str | None = Field(None, alias="asOf")
    source: list[str] | None = None
    is_stale: bool | None = Field(None, alias="isStale")
    period_end: str | None = Field(None, alias="periodEnd")
    unavailable_reason: str | None = Field(None, alias="unavailableReason")


class KpiSet(Provenance):
    module: str
    as_of: str = Field(alias="asOf")
    scope: Scope
    kpis: list[Kpi]


# ------------------------------------------------------------------ series


class Series(ContractModel):
    key: str
    name: str
    kind: Literal["line", "bar", "area", "band", "scatter", "step"]
    axis: int | None = Field(None, ge=0, le=1)
    color: str | None = None
    dash: bool | None = None
    stack: str | None = None
    smooth: bool | None = None
    symbol: bool | None = None
    data: list[list[XVal | None]]
    # additions (database category charts): the series' own unit, provenance and source column
    unit: str | None = None
    as_of: str | None = Field(None, alias="asOf")
    is_stale: bool | None = Field(None, alias="isStale")
    period_end: str | None = Field(None, alias="periodEnd")
    source: list[str] | None = None  # ["table.column"]
    resolution: Literal["daily", "monthly", "annual"] | None = None
    status: Literal["live", "withheld", "duplicate"] | None = None
    chart: Literal["bar", "diverging-bar", "line"] | None = None  # recommended chart type (BACKEND_ISSUES #8)


class Axis(ContractModel):
    unit: str
    label: str | None = None
    min: float | None = None
    max: float | None = None
    inverse: bool | None = None
    log: bool | None = None


class Band(ContractModel):
    from_: float = Field(alias="from")
    to: float
    color: str
    label: str


class RefLine(ContractModel):
    y: float
    label: str
    axis: float | None = None
    color: str | None = None


class SeriesSet(Provenance):
    id: str
    title: str | None = None
    x_type: Literal["time", "category", "value"] = Field(alias="xType")
    x_unit: str | None = Field(None, alias="xUnit")
    x_log: bool | None = Field(None, alias="xLog")
    axes: list[Axis] = Field(min_length=1, max_length=2)
    series: list[Series]
    bands: list[Band] | None = None
    ref_lines: list[RefLine] | None = Field(None, alias="refLines")
    as_of: str | None = Field(None, alias="asOf")
    note: str | None = None
    # additions (category charts): the group's recommended chart and its min / max pair
    chart: Literal["bar", "diverging-bar", "line", "band", "mixed"] | None = None
    band: list[str] | None = None


# ------------------------------------------------------------------ categories


class CategorySeries(ContractModel):
    key: str
    name: str
    values: list[Num]
    color: str | None = None
    stack: str | None = None
    kind: Literal["bar", "line"] | None = None
    axis: float | None = None


class Center(ContractModel):
    value: str
    label: str


class CategorySet(Provenance):
    id: str
    title: str | None = None
    unit: str
    units: list[str] | None = None  # addition: one unit per category when they differ (`unit` is then "mixed")
    categories: list[str]
    colors: list[str] | None = None
    series: list[CategorySeries]
    center: Center | None = None
    axis2_unit: str | None = Field(None, alias="axis2Unit")
    as_of: str | None = Field(None, alias="asOf")  # addition


# ------------------------------------------------------------------ tables


class Column(ContractModel):
    key: str
    label: str
    type: Literal["text", "number", "class", "trend", "delta", "bar", "date", "rank"]
    unit: str | None = None
    decimals: float | None = None
    scale: str | None = None
    good_direction: Literal["up", "down", "neutral"] | None = Field(None, alias="goodDirection")
    align: Literal["left", "right", "center"] | None = None


class TableSet(Provenance):
    id: str
    title: str | None = None
    columns: list[Column]
    rows: list[dict[str, Cell]]
    row_key: str | None = Field(None, alias="rowKey")
    link_to: str | None = Field(None, alias="linkTo")
    total: float | None = None
    as_of: str | None = Field(None, alias="asOf")  # addition


# ------------------------------------------------------------------ matrix


class MatrixSet(Provenance):
    id: str
    title: str | None = None
    rows: list[str]
    cols: list[str]
    values: list[list[Cell]]
    unit: str
    scale: str
    as_of: str | None = Field(None, alias="asOf")  # addition


# ------------------------------------------------------------------ maps


class MapPoint(ContractModel):
    id: str
    name: str
    lat: float
    lon: float
    value: Cell
    size: float | None = None
    kind: str | None = None
    sub: str | None = None


class LegendClass(ContractModel):
    """One class of a data-derived map legend (addition): values in [from, to); open ends are null."""

    from_: float | None = Field(alias="from")
    to: float | None
    color: str
    label: str


class MapLayer(Provenance):
    id: str
    title: str
    kind: Literal["choropleth", "grid", "points"]
    level: Literal["province", "wma", "catchment", "municipality", "grid", "point"]
    scale: str
    unit: str
    date: str
    values: dict[str, Cell] | None = None
    cell_size: float | None = Field(None, alias="cellSize")
    cells: list[tuple[float, float, float | None]] | None = None
    points: list[MapPoint] | None = None
    as_of: str | None = Field(None, alias="asOf")  # addition
    geometry: str | None = None  # addition: boundary set the `values` keys refer to, e.g. "districts"
    legend: list[LegendClass] | None = None  # addition: class breaks derived from the data (overrides `scale`'s)


# ------------------------------------------------------------------ entities


class Entity(ContractModel):
    id: str
    name: str
    type: str
    province: str | None = None
    wma: str | None = None
    municipality: str | None = None
    lat: float | None = None
    lon: float | None = None
    sub: str | None = None


class EntityList(Provenance):
    type: str
    total: float
    items: list[Entity]


class EntityField(ContractModel):
    label: str
    value: Cell
    unit: str | None = None
    scale: str | None = None
    # additions
    as_of: str | None = Field(None, alias="asOf")
    is_stale: bool | None = Field(None, alias="isStale")


class EntityDetail(Entity):
    title: str
    status: Status | None = None
    fields: list[EntityField]
    image: (
        Literal["dam", "river", "borehole", "wwtw", "wtp", "settlement", "station", "aquifer", "municipality"] | None
    ) = None
    # additions
    as_of: str | None = Field(None, alias="asOf")
    source: list[str] | None = None
    unavailable: list[UnavailableField] | None = None  # fields returned as null, with the reason (`fields.<label>`)


# ------------------------------------------------------------------ database category view (addition)


class CatalogueVariable(ContractModel):
    """One database column (docs/CATEGORY_CATALOGUE.md)."""

    column: str  # exact database column name (the `vars` value)
    label: str  # from the column name, with the unit: "Daily Rainfall (mm/day)"
    name: str  # the label without the unit
    unit: str | None = None
    kind: Literal["numeric", "categorical"]
    resolution: Literal["daily", "monthly", "annual"]
    status: Literal["live", "withheld", "duplicate"]
    reason: str | None = None  # withheld: why
    duplicate_of: str | None = Field(None, alias="duplicateOf")  # duplicate: "table.column" that is shown instead
    group: str | None = None  # chart group id (None for duplicates)
    default_visible: bool = Field(alias="defaultVisible")
    chart: Literal["bar", "diverging-bar", "line"] = "line"  # recommended chart type
    as_of: str | None = Field(None, alias="asOf")
    is_stale: bool | None = Field(None, alias="isStale")
    period_end: str | None = Field(None, alias="periodEnd")
    source: str  # "table.column"
    note: str | None = None
    unit_override: str | None = Field(None, alias="unitOverride")  # why the unit differs from the name's suffix


class CatalogueGroup(ContractModel):
    id: str
    label: str
    unit: str  # the group's single axis unit ("index" for unitless indices, "—" for other unitless columns)
    resolution: Literal["daily", "monthly", "annual"]
    columns: list[str]
    default_visible: list[str] = Field(alias="defaultVisible")
    path: str  # the series endpoint for this group
    chart: Literal["bar", "diverging-bar", "line", "band", "mixed"] = "line"
    band: list[str] | None = None  # [min column, max column] when chart is "band"


class CatalogueTable(ContractModel):
    id: str
    table: str
    label: str
    spec: str | None = None  # spec section, e.g. "3.1"
    spec_tag: str | None = Field(None, alias="specTag")  # "§3.1"
    modules: list[str]
    resolution: Literal["daily", "dekadal", "monthly", "annual"]
    as_of: str | None = Field(None, alias="asOf")
    is_stale: bool | None = Field(None, alias="isStale")
    counts: dict[str, int]  # live / withheld / duplicate
    groups: list[CatalogueGroup]
    variables: list[CatalogueVariable]


class CategoryList(Provenance):
    module: str
    as_of: str | None = Field(None, alias="asOf")
    tables: list[CatalogueTable]


# ------------------------------------------------------------------ alerts, search, reference


class Alert(ContractModel):
    id: str
    date: str
    module: str
    severity: Literal["critical", "high", "moderate", "low", "info"]
    title: str
    message: str
    location: str
    province: str | None = None
    entity_id: str | None = Field(None, alias="entityId")
    lat: float | None = None
    lon: float | None = None
    status: Literal["open", "acknowledged", "assigned", "resolved"]


class AlertList(ContractModel):
    total: float
    unread: float
    items: list[Alert]


class SearchHit(ContractModel):
    type: str
    id: str
    label: str
    sublabel: str | None = None
    path: str
    params: dict[str, str] | None = None


class SearchResult(ContractModel):
    q: str
    hits: list[SearchHit]


class RefItem(ContractModel):
    id: str
    name: str
    parent: str | None = None
    province: str | None = None
    wma: str | None = None


class RefList(ContractModel):
    type: str
    items: list[RefItem]


# ------------------------------------------------------------------ error bodies (documented in OpenAPI)


class ErrorBody(BaseModel):
    error: str
    code: str | None = None
    issues: list[Any] | None = None
    detail: str | None = None
    missing: list[str] | None = None
