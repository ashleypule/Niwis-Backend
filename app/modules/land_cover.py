"""Land-cover shares of a scope, derived from `9_2_catchment_characteristics_master` (audit step 3, DECISIONS D49).

The table holds one land-cover profile per district (shares in %, constant over the record, summing to 100 %). A scope's
share of a class = Σ (catchment area × share) / Σ catchment area over its districts, the table's own catchment area as
the weight (a WMA takes the district's fraction inside the WMA). `cropland_agri_pct` is withheld (identical to grassland
in every row) and the other seven classes already sum to 100 %, so cropland cannot be separated from "Grassland".

The table has no NDVI per class. The `ndvi` view is therefore a derived pairing: the district NDVI weighted by how much
of the class each district holds. It is meaningless for one district (every class would read the district's NDVI), so a
single-district scope answers null with the reason.
"""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd

from app.core.aggregate import MIN_COVERAGE, sum_fractions
from app.core.provenance import iso, latest_complete_period_end
from app.core.stats import rnd
from app.modules.common import Ctx
from app.schemas import contract as c

AREA = "cc_catchment_area_km2"
TABLE = "9_2_catchment_characteristics_master"
# class label (the catalogue's column meaning) → registry variable, colour (`vegetationCover` palette)
CLASSES: tuple[tuple[str, str, str], ...] = (
    ("Tree cover", "cc_tree_cover_pct", "#1B5E20"),
    ("Shrubland / fynbos", "cc_shrubland_fynbos_pct", "#2E9E5B"),
    ("Grassland", "cc_grassland_pct", "#C9A227"),
    ("Wetland", "cc_wetland_pct", "#4FC3F7"),
    ("Water bodies", "cc_water_bodies_pct", "#1565C0"),
    ("Built-up / urban", "cc_builtup_urban_pct", "#9E9E9E"),
    ("Bare soil / mining", "cc_bare_soil_mining_pct", "#D7CCC8"),
)
Kind = Literal["share", "ndvi"]
METHOD = (
    "Derived from 9_2: Σ (catchment area × district share) / Σ catchment area. One land-cover profile per district, "
    "constant over the record; cropland is withheld (a copy of grassland), so Grassland may include it."
)


async def _year_frame(ctx: Ctx, var: str, year: int) -> pd.Series:
    df = await ctx.data.annual(var)
    return df[df["date"].dt.year == year].set_index("district")["value"].dropna()


async def weighted_shares(ctx: Ctx) -> tuple[dict[str, float | None], dict[str, dict[str, float]], int]:
    """Scope share (%) per class, each class's weight per district (area × share), and the year read."""
    newest = ctx.newest_db
    last_year = (await ctx.data.annual(AREA))["date"].dt.year.max()
    year = int(min(last_year, latest_complete_period_end(newest, "annual").year)) if newest else int(last_year)
    areas = await _year_frame(ctx, AREA, year)
    fractions = sum_fractions(ctx.filters.scope, ctx.state.spatial)
    w = {k: f * float(areas[k]) for k, f in fractions.items() if k in areas.index}
    total = sum(fractions[k] * float(areas.get(k, 0.0)) for k in fractions)
    covered = bool(fractions) and len(w) / len(fractions) >= MIN_COVERAGE and total > 0
    shares: dict[str, float | None] = {}
    per_class: dict[str, dict[str, float]] = {}
    for label, var, _ in CLASSES:
        vals = await _year_frame(ctx, var, year)
        per_class[label] = {k: w[k] * float(vals[k]) / 100 for k in w if k in vals.index}
        shares[label] = sum(per_class[label].values()) / sum(w.values()) * 100 if covered and w else None
    return shares, per_class, year


async def land_cover_set(ctx: Ctx, kind: Kind = "share", *, set_id: str = "land-cover") -> dict[str, Any]:
    """The scope's land-cover breakdown as a category set."""
    shares, per_class, year = await weighted_shares(ctx)
    variables = [AREA, *[v for _, v, _ in CLASSES]]
    labels = [label for label, _, _ in CLASSES]
    tables = [TABLE]
    if kind == "share":
        title = f"Land cover, share of area ({year})"
        unit, name, nd = "%", "Share of area", 1
        values = [rnd(shares[x], nd) for x in labels]
        for x in labels:
            if shares[x] is None:
                ctx.explain("series.a", "9_2 has no land-cover profile for enough of this scope's area")
        note = METHOD
    else:
        ndvi = await ctx.data.monthly("ndvi")
        month = pd.Timestamp(ndvi["date"].max())
        cur = ndvi[ndvi["date"] == month].set_index("district")["value"].dropna()
        tables.append(ctx.data.table_of("ndvi"))
        variables.append("ndvi")
        title = f"District NDVI weighted by land-cover share ({month:%b %Y})"
        unit, name, nd = "index", "NDVI, weighted by class share", 3
        if len(ctx.weights()) == 1:
            values = [None] * len(labels)
            ctx.explain("series.a", "One district has one NDVI in the database: it cannot be split by land-cover class")
        else:
            values = []
            for x in labels:
                num = sum(a * float(cur[k]) for k, a in per_class[x].items() if k in cur.index)
                den = sum(a for k, a in per_class[x].items() if k in cur.index)
                values.append(rnd(num / den, nd) if den else None)
        note = (
            "Derived: NDVI is not stored per land-cover class. Each bar is the district NDVI weighted by the area of "
            "the class in each district (9_2), not an NDVI measured over that class. " + METHOD
        )
    as_of = pd.Timestamp(year, 1, 1).date()
    prov = ctx.provenance(as_of, sorted(set(tables)), res="annual")
    prov.pop("asOf")
    return c.CategorySet(
        id=set_id,
        title=title,
        unit=unit,
        categories=labels,
        colors=[col for _, _, col in CLASSES],
        series=[c.CategorySeries(key="a", name=name, values=values)],
        as_of=iso(as_of),
        derived=True,
        method_note=note,
        **prov,
    ).dump()
