"""Shared machinery of the compliance modules (green drop, blue drop, RWQO; DECISIONS D43).

Each module compares the stored monthly district values of its parameters with published limits held in
`app/core/thresholds.json` (effluent general limits, SANS 241, the RWQO objectives the 15.x tables state) and derives
a **district compliance score**: the share of its judged parameters within their limit in a month (0–100 %). Scores
are banded with the frontend's Green Drop / Blue Drop bands or shown as a percentage. They are statistical
compliance rates of district aggregates, never the official DWS assessment scores (which are not in the database);
every panel says so. There are no plants, supply systems or sites in the database: "entities" are the 52 districts.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any, ClassVar

import pandas as pd

from app.core import districts
from app.core.aggregate import weighted_scalar
from app.core.provenance import iso
from app.core.stats import rnd
from app.core.thresholds import thresholds
from app.modules.district import NO_POINTS, DistrictModule
from app.schemas import contract as c

BAND_COLORS = {
    "Excellent": "#1565C0",
    "Good": "#2E9E5B",
    "Moderate": "#F6CF3E",
    "Poor": "#F28C28",
    "Critical": "#B2182B",
}
BANDS = ["Excellent", "Good", "Moderate", "Poor", "Critical"]
GROUPS = {"Excellent / Good": ("Excellent", "Good"), "Moderate": ("Moderate",), "Poor / Critical": ("Poor", "Critical")}


def limit_entry(table: str, pid: str) -> dict[str, Any]:
    """A limit / objective entry by id from `sans241` (a list) or `effluent_limits` / `rwqo_objectives` (`params`)."""
    cfg = thresholds()[table]
    items = cfg if isinstance(cfg, list) else cfg["params"]
    return next(x for x in items if x["id"] == pid)


def limit_text(lim: dict[str, Any]) -> str:
    lo, hi = lim.get("min"), lim.get("max")
    if lo is not None and hi is not None:
        return f"{lo:g} – {hi:g} {lim['unit']}"
    if lo is not None:
        return f"≥ {lo:g} {lim['unit']}"
    return f"≤ {hi:g} {lim['unit']}"


class ComplianceService(DistrictModule):
    """Base of the compliance modules: `PARAMS` (parameter id → registry variable, all judged against
    `LIMITS` with `judge`), `band` (score → class), `SCALE` (the frontend scale of the class) and `SCORE_NOTE`."""

    PARAMS: ClassVar[dict[str, str]] = {}
    LIMITS: ClassVar[str] = ""
    SCALE: ClassVar[str] = ""
    SCORE_LABEL: ClassVar[str] = "compliance"
    SCORE_NOTE: ClassVar[str] = ""

    @staticmethod
    def judge(pid: str, value: float) -> bool:
        raise NotImplementedError

    @staticmethod
    def band(score: float) -> str:
        raise NotImplementedError

    # ---------------------------------------------------------------- data

    async def last(self) -> pd.Timestamp:
        """Latest complete month common to the judged parameters."""
        return min([await self.latest_month(v) for v in self.PARAMS.values()])

    async def tests(self) -> pd.DataFrame:
        """district, date, param, value, pass — every judged value up to the latest common month (all districts)."""

        async def load() -> pd.DataFrame:
            last = await self.last()
            parts = []
            for pid, var in self.PARAMS.items():
                df = await self.monthly_frame(var)
                df = df[(df["date"] <= last) & df["value"].notna()]
                parts.append(df.assign(param=pid, passed=df["value"].map(lambda v, p=pid: self.judge(p, float(v)))))
            out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            return out[["district", "date", "param", "value", "passed"]] if not out.empty else out

        res: pd.DataFrame = await self.ctx.state.cache.get_or_set(
            f"{self.MODULE}:tests", self.ctx.state.settings.cache_ttl_long_series_s, load
        )
        return res

    async def scores(self) -> pd.DataFrame:
        """district, date, score (% of judged parameters within limit that month)."""
        t = await self.tests()
        if t.empty:
            return pd.DataFrame(columns=["district", "date", "score"])
        g = t.groupby(["district", "date"], as_index=False)["passed"].mean()
        return g.rename(columns={"passed": "score"}).assign(score=lambda x: x["score"] * 100)

    def mine(self, df: pd.DataFrame) -> pd.DataFrame:
        sel = self.ctx.filters.scope
        return df if sel.level == "national" else df[df["district"].isin(sel.district_codes)]

    async def district_scores(self, month: pd.Timestamp) -> pd.Series:
        s = await self.scores()
        return s[s["date"] == month].set_index("district")["score"]

    async def scope_score(self, month: pd.Timestamp) -> float | None:
        return weighted_scalar(await self.district_scores(month), self.ctx.weights())

    async def param_share(self, pid: str, month: pd.Timestamp) -> float | None:
        """Share (%) of the scope's districts whose value of `pid` is within its limit in `month`."""
        t = self.mine(await self.tests())
        sub = t[(t["date"] == month) & (t["param"] == pid)]
        return float(sub["passed"].mean() * 100) if len(sub) else None

    def prov(self, month: pd.Timestamp, *pids: str, aggregation: str | None = None) -> dict[str, Any]:
        variables = [self.PARAMS[p] for p in (pids or tuple(self.PARAMS))]
        p = self.ctx.provenance(month.date(), self.src(*variables), res="monthly")
        p.pop("asOf")
        if aggregation:
            p["aggregation"] = aggregation
        return p

    def status(self, score: float | None) -> c.Status | None:
        if score is None:
            return None
        b = self.band(score)
        return c.Status(scale=self.SCALE, id=b, label=b)

    # ---------------------------------------------------------------- shared panels

    async def trend(self, unit_label: str) -> dict[str, Any]:
        """Districts per band group and the mean score per year (`timeseries?metric=trend`, x: category)."""
        s = self.mine(await self.scores())
        last = await self.last()
        s = s[s["date"] <= last]
        if s.empty:
            self.ctx.explain("series.rate", "No judged values for this scope")
        yearly = s.assign(year=s["date"].dt.year).groupby(["year", "district"])["score"].mean().reset_index()
        years = sorted(yearly["year"].unique())
        counts: dict[str, list[float]] = {g: [] for g in GROUPS}
        rate: list[float | None] = []
        for y in years:
            sub = yearly[yearly["year"] == y]
            bands = sub["score"].map(self.band)
            for g, members in GROUPS.items():
                counts[g].append(float(bands.isin(members).sum()))
            rate.append(rnd(float(sub["score"].mean()), 1))
        series = [
            c.CategorySeries(
                key=g.lower().replace(" / ", "-"),
                name=g,
                values=counts[g],
                stack="bands",
                color=BAND_COLORS[GROUPS[g][0]],
            )
            for g in GROUPS
        ]
        series.append(c.CategorySeries(key="rate", name="Compliance rate (%)", values=rate, kind="line", axis=1))
        # optional contract fields are omitted, never null (the frontend's schemas use .optional())
        return c.SeriesSet(
            id="trend",
            title=f"Districts by {self.SCORE_LABEL} band and mean score per year",
            x_type="category",
            axes=[
                c.Axis(unit=unit_label, label="Districts"),
                c.Axis(unit="%", label="Compliance rate", min=0, max=100),
            ],
            series=[
                c.Series(
                    key=x.key,
                    name=x.name,
                    kind="bar" if x.kind != "line" else "line",
                    axis=int(x.axis) if x.axis is not None else 0,
                    **({"stack": x.stack} if x.stack else {}),
                    **({"color": x.color} if x.color else {}),
                    data=[[str(y), v] for y, v in zip(years, x.values, strict=True)],
                )
                for x in series
            ],
            as_of=iso(last.date()),
            note=self.SCORE_NOTE,
            **self.prov(last, aggregation="district"),
        ).dump()

    async def band_breakdown(self, unit_label: str) -> dict[str, Any]:
        last = await self.last()
        scores = self.mine((await self.scores())[lambda d: d["date"] == last])
        bands = scores["score"].map(self.band)
        if scores.empty:
            self.ctx.explain("series.count", "No judged values for this scope in the latest month")
        return c.CategorySet(
            id="breakdown",
            title=f"Districts by {self.SCORE_LABEL} band – {last:%b %Y}",
            unit=unit_label,
            categories=BANDS,
            colors=[BAND_COLORS[b] for b in BANDS],
            series=[c.CategorySeries(key="count", name="Districts", values=[float((bands == b).sum()) for b in BANDS])],
            as_of=iso(last.date()),
            **self.prov(last, aggregation="district"),
        ).dump()

    async def by_province(self) -> dict[str, Any]:
        last = await self.last()
        per = await self.district_scores(last)
        areas = self.ctx.state.spatial.district_areas() or {}
        vals = []
        for p in districts.PROVINCES:
            w = {d.code: areas.get(d.code, 1.0) for d in districts.in_province(p)}
            vals.append(rnd(weighted_scalar(per, w), 1))
        self.ctx.explain("series.score", "A province mean needs at least 95 % of the province's area")
        return c.CategorySet(
            id="by-province",
            title=f"Mean {self.SCORE_LABEL} score by province – {last:%b %Y}",
            unit="%",
            categories=list(districts.PROVINCES.values()),
            series=[c.CategorySeries(key="score", name=f"Mean {self.SCORE_LABEL} score", values=vals)],
            as_of=iso(last.date()),
            **self.prov(last, aggregation="district-area-weighted"),
        ).dump()

    async def param_compliance(self, title: str) -> dict[str, Any]:
        last = await self.last()
        cats = [limit_entry(self.LIMITS, p)["label"] for p in self.PARAMS]
        shares = [rnd(await self.param_share(p, last), 1) for p in self.PARAMS]
        return c.CategorySet(
            id="param-compliance",
            title=f"{title} – {last:%b %Y}",
            unit="%",
            categories=cats,
            series=[c.CategorySeries(key="share", name="Districts within limit", values=shares)],
            as_of=iso(last.date()),
            **self.prov(last, aggregation="district"),
        ).dump()

    async def latest_table(self, table_id: str, title: str, limit_label: str) -> dict[str, Any]:
        """One row per parameter: the scope's area-weighted mean value, its limit, the share of districts within it."""
        last = await self.last()
        rows = []
        for pid, var in self.PARAMS.items():
            lim = limit_entry(self.LIMITS, pid)
            vals = await self.district_values(var, last)
            mean = weighted_scalar(vals, self.ctx.weights())
            share = await self.param_share(pid, last)
            rows.append(
                {
                    "id": pid,
                    "parameter": lim["label"],
                    "value": rnd(mean, 3),
                    "unit": lim["unit"],
                    "limit": limit_text(lim),
                    "status": None
                    if share is None
                    else ("Within limit" if share >= 100 else f"{share:.0f} % of districts within limit"),
                    "compliance": rnd(share, 0),
                    "sampleDate": iso(last.date()),
                }
            )
        if all(r["value"] is None for r in rows):
            self.ctx.explain("rows.value", "No values for this scope in the latest month")
        return c.TableSet(
            id=table_id,
            title=f"{title} – {last:%b %Y}",
            columns=[
                c.Column(key="parameter", label="Parameter", type="text"),
                c.Column(key="value", label="Value", type="number", decimals=3, align="right"),
                c.Column(key="unit", label="Unit", type="text"),
                c.Column(key="limit", label=limit_label, type="text"),
                c.Column(key="status", label="Status", type="text"),
                c.Column(key="compliance", label="Districts within limit", type="number", unit="%", decimals=0),
                c.Column(key="sampleDate", label="Month", type="date"),
            ],
            rows=rows,
            row_key="id",
            as_of=iso(last.date()),
            **self.prov(last, aggregation="district-area-weighted"),
        ).dump()

    async def ranking_table(self, title: str, name_label: str, limit: int | None) -> dict[str, Any]:
        last = await self.last()
        per = self.mine((await self.scores())[lambda d: d["date"] == last]).sort_values("score", ascending=False)
        rows = []
        for i, (_, r) in enumerate(per.iterrows(), start=1):
            d = districts.by_code(str(r["district"]))
            if d is None:
                continue
            rows.append(
                {
                    "id": d.code,
                    "rank": i,
                    "name": d.official_name,
                    "municipality": districts.PROVINCES[d.province],
                    "score": rnd(float(r["score"]), 1),
                    "status": self.band(float(r["score"])),
                }
            )
        return c.TableSet(
            id="ranking",
            title=f"{title} – {last:%b %Y}",
            columns=[
                c.Column(key="rank", label="#", type="rank"),
                c.Column(key="name", label=name_label, type="text"),
                c.Column(key="municipality", label="Province", type="text"),
                c.Column(key="score", label="Score (%)", type="number", decimals=1, align="right"),
                c.Column(key="status", label="Status", type="class", scale=self.SCALE),
            ],
            rows=rows[: limit or 20],
            row_key="id",
            link_to="entity",
            total=len(rows),
            as_of=iso(last.date()),
            **self.prov(last, aggregation="district"),
        ).dump()

    async def score_map(self, layer: str, title: str) -> dict[str, Any]:
        last = await self.last()
        per = await self.district_scores(last)
        sel = self.ctx.filters.scope
        keep = set(sel.district_codes)
        out = {str(k): rnd(float(v), 1) for k, v in per.items() if k in keep}
        if not out:
            self.ctx.explain("values", f"No judged values for these districts in {last:%b %Y}")
        return c.MapLayer(
            id=layer,
            title=f"{title} – {last:%b %Y}",
            kind="choropleth",
            level="municipality",
            scale=self.SCALE,
            unit="%",
            date=iso(last.date()) or "",
            values=out,
            geometry="districts",
            **self.prov(last, aggregation="district"),
        ).dump()

    async def entity_card(self, code: str, kind: str, image: Any, sub: str) -> dict[str, Any]:
        d = self.district(code)
        last = await self.last()
        score = (await self.district_scores(last)).get(d.code)
        fields = [c.EntityField(label="Province", value=districts.PROVINCES[d.province])]
        fields.append(
            c.EntityField(
                label=f"{self.SCORE_LABEL.capitalize()} score ({last:%b %Y})",
                value=rnd(float(score), 1) if score is not None and pd.notna(score) else None,
                unit="%",
            )
        )
        for pid, var in self.PARAMS.items():
            fields.append(await self.field(limit_entry(self.LIMITS, pid)["label"], var, d.code, nd=3))
        st = self.status(float(score)) if score is not None and pd.notna(score) else None
        return c.EntityDetail(
            id=d.code,
            name=d.official_name,
            type=kind,
            province=d.province,
            sub=f"{districts.PROVINCES[d.province]} · MDB {d.code} · {sub}",
            title=d.official_name,
            fields=fields,
            image=image,
            **({"status": st} if st else {}),
            as_of=iso(last.date()),
            source=self.src(*self.PARAMS.values()),
        ).dump()

    def no_points(self, what: str) -> str:
        return f"{NO_POINTS}: {what}"


def month_of(ts: pd.Timestamp | dt.date) -> dt.date:
    d: dt.date = pd.Timestamp(ts).date()
    return d


JudgeFn = Callable[[str, float], bool]
