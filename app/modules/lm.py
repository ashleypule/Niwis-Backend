"""Helpers shared by the modules whose tables are keyed by local municipality (services, tariffs; DECISIONS D46).

District values come from the registry readers (rows aggregated per district by each column's `agg`); the entity
cards read the municipality's own latest row, so the 213 local municipalities and metros are the pages' entities.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from app.core import districts, municipalities
from app.core.provenance import iso
from app.core.stats import rnd
from app.db import ident
from app.modules.district import DistrictModule
from app.registry.registry import Dataset
from app.schemas import contract as c


class LocalMunicipalModule(DistrictModule):
    """A district module whose tables hold local municipalities: province aggregates, ratios of sums and the
    municipality entities."""

    ENTITY_TYPE = "municipality"

    # ---------------------------------------------------------------- aggregates

    async def total(self, var: str, year: pd.Timestamp) -> float | None:
        """Sum over the scope's districts (every district needed)."""
        return self.aggregate(await self.district_values(var, year), True)

    async def ratio(self, num: str, den: str, year: pd.Timestamp) -> float | None:
        """Σ num / Σ den over the scope's districts that hold both (%)."""
        a, b = await self.district_values(num, year), await self.district_values(den, year)
        both = a.index.intersection(b.index)
        a, b = a[both].dropna(), b[both].dropna()
        both = a.index.intersection(b.index)
        sel = self.ctx.filters.scope
        if sel.level != "national":
            both = both.intersection(pd.Index(sel.district_codes))
        if len(both) == 0 or float(b[both].sum()) <= 0:
            return None
        return float(a[both].sum() / b[both].sum() * 100)

    @staticmethod
    def province_weights(p: str) -> dict[str, float]:
        return {d.code: 1.0 for d in districts.in_province(p)}

    async def by_province_ratio(self, num: str, den: str, year: pd.Timestamp) -> list[float | None]:
        a, b = await self.district_values(num, year), await self.district_values(den, year)
        out: list[float | None] = []
        for p in districts.PROVINCES:
            codes = [d.code for d in districts.in_province(p) if d.code in a.index and d.code in b.index]
            sa, sb = float(a[codes].sum()) if codes else 0.0, float(b[codes].sum()) if codes else 0.0
            out.append(rnd(sa / sb * 100, 1) if codes and sb > 0 else None)
        return out

    @staticmethod
    def mean_available(per: pd.Series, weights: dict[str, float]) -> float | None:
        """Area-weighted mean over the districts of `weights` that hold a value — without the 95 % coverage rule:
        the municipal tables cover only the districts their municipalities lie in (D46)."""
        s = per[per.index.isin(weights)].dropna()
        if s.empty:
            return None
        w = pd.Series({k: weights[k] for k in s.index})
        return float((s * w).sum() / w.sum())

    def available_note(self, per: pd.Series) -> str:
        n = int(per[per.index.isin(self.ctx.weights())].notna().sum())
        return f"{n} of {len(self.ctx.weights())} districts with data"

    async def by_province_mean(self, var: str, year: pd.Timestamp, nd: int = 2) -> list[float | None]:
        per = await self.district_values(var, year)
        areas = self.ctx.state.spatial.district_areas() or {}
        return [
            rnd(self.mean_available(per, {d.code: areas.get(d.code, 1.0) for d in districts.in_province(p)}), nd)
            for p in districts.PROVINCES
        ]

    async def by_province_sum(
        self, var: str, year: pd.Timestamp, scale: float = 1.0, nd: int = 1
    ) -> list[float | None]:
        per = await self.district_values(var, year)
        out: list[float | None] = []
        for p in districts.PROVINCES:
            vals = [per.get(d.code) for d in districts.in_province(p)]
            out.append(
                rnd(float(sum(vals)) * scale, nd) if vals and all(v is not None and pd.notna(v) for v in vals) else None
            )
        return out

    async def scope_mean(self, var: str, year: pd.Timestamp) -> float | None:
        """Area-weighted mean over the scope's districts that hold a value."""
        return self.mean_available(await self.district_values(var, year), self.ctx.weights())

    def prov(self, when: pd.Timestamp, *variables: str, aggregation: str = "district") -> dict[str, Any]:
        p = self.ctx.provenance(when.date(), self.src(*variables), res="annual")
        p.pop("asOf")
        p["aggregation"] = aggregation
        return p

    def category_set(
        self,
        cid: str,
        title: str,
        unit: str,
        cats: list[str],
        series: list[c.CategorySeries],
        when: pd.Timestamp,
        *variables: str,
        aggregation: str = "district",
    ) -> dict[str, Any]:
        return c.CategorySet(
            id=cid,
            title=title,
            unit=unit,
            categories=cats,
            series=series,
            as_of=iso(when.date()),
            **self.prov(when, *variables, aggregation=aggregation),
        ).dump()

    async def ratio_map(
        self, layer: str, title: str, num: str, den: str, scale: str, year: pd.Timestamp
    ) -> dict[str, Any]:
        a, b = await self.district_values(num, year), await self.district_values(den, year)
        keep = set(self.ctx.filters.scope.district_codes)
        out = {
            str(k): rnd(float(a[k]) / float(b[k]) * 100, 1)
            for k in a.index
            if k in keep and k in b.index and pd.notna(a[k]) and pd.notna(b[k]) and float(b[k]) > 0
        }
        if not out:
            self.ctx.explain("values", f"No values for these districts in {year:%Y}")
        prov = self.ctx.provenance(year.date(), self.src(num, den), res="annual")
        prov["aggregation"] = "district"
        return c.MapLayer(
            id=layer,
            title=f"{title} – {year:%Y}",
            kind="choropleth",
            level="municipality",
            scale=scale,
            unit="%",
            date=iso(year.date()) or "",
            values=out,
            geometry="districts",
            **prov,
        ).dump()

    # ---------------------------------------------------------------- local-municipality entities

    def lm_entities(self) -> dict[str, Any]:
        sel = self.ctx.filters.scope
        items = [
            c.Entity(
                id=m.lm_id,
                name=m.name,
                type=self.ENTITY_TYPE,
                province=m.province,
                municipality=m.district_code,
                sub=f"{districts.PROVINCES[m.province]} · {district_name(m.district_code)}",
            )
            for m in sorted(municipalities.municipalities(), key=lambda m: m.name)
            if sel.level == "national" or m.district_code in sel.district_codes
        ]
        return c.EntityList(type=self.ENTITY_TYPE, total=len(items), items=items).dump()

    async def lm_spellings(self, ds: Dataset) -> dict[str, tuple[str, ...]]:
        """LM id → the table's own spellings of that municipality (cached a day)."""
        loc = ds.location_sql()

        async def load() -> dict[str, tuple[str, ...]]:
            out: dict[str, set[str]] = {}
            for r in await self.ctx.state.db.fetch(f"SELECT DISTINCT {loc} FROM {ds.fq} WHERE {loc} IS NOT NULL"):
                m = municipalities.lm_of(str(r[0]))
                if m is not None:
                    out.setdefault(m.lm_id, set()).add(str(r[0]))
            return {k: tuple(sorted(v)) for k, v in out.items()}

        res: dict[str, tuple[str, ...]] = await self.ctx.state.cache.get_or_set(f"lm:spellings:{ds.table}", 86400, load)
        return res

    async def lm_latest(
        self, ds: Dataset, lm_id: str, columns: list[str], cap: pd.Timestamp | None = None
    ) -> tuple[dict[str, float | None], dt.date | None]:
        """The municipality's newest served row of `columns` (each column's valid expression) up to the end of
        `cap`'s year (the module's latest complete year, D5), and its date."""
        names = list((await self.lm_spellings(ds)).get(lm_id, ()))
        if not names:
            return {}, None
        exprs = ", ".join(f"{ds.column_sql(col)} AS v{i}" for i, col in enumerate(columns))
        valid = " AND ".join(dict.fromkeys(ds.valid_sql(col) for col in columns))
        capped = f" AND {ds.date_sql()} <= DATE '{cap.year}-12-31'" if cap is not None else ""
        sql = (
            f"SELECT {ds.date_sql()} AS d, {exprs} FROM {ds.fq} WHERE {valid}{capped} "
            f"AND {ds.location_sql()} = ANY($1::text[]) ORDER BY {ds.date_sql()} DESC LIMIT 1"
        )
        row = await self.ctx.state.db.fetchrow(sql, names)
        if row is None:
            return {}, None
        return {col: (None if row[f"v{i}"] is None else float(row[f"v{i}"])) for i, col in enumerate(columns)}, row["d"]

    def lm_field(
        self, label: str, value: float | None, unit: str | None, when: dt.date | None, nd: int = 1
    ) -> c.EntityField:
        return c.EntityField(
            label=f"{label} ({when:%Y})" if when else label,
            value=rnd(value, nd) if value is not None and pd.notna(value) else None,
            **({} if not unit or unit == "—" else {"unit": unit}),
            **({"as_of": iso(when)} if when else {}),
        )


def quoted(column: str) -> str:
    return ident(column)


def district_name(code: str) -> str:
    d = districts.by_code(code)
    return d.official_name if d else code
