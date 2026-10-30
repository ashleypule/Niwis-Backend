"""District-level data access from each variable's canonical source (app/registry/datasets.yaml).

Every loader returns a pandas DataFrame with columns `district` (MDB code), `date` (datetime64, first day of the
period for monthly/annual data) and `value`, cached for `cache_ttl_long_series_s`. District spellings are normalised
through app.core.districts. Monthly / annual values repeated on daily rows (`repeats`) are read once per period.
Withheld or missing variables raise DataUnavailable — never placeholder numbers.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

import pandas as pd

from app.config import Settings
from app.core import districts, municipalities
from app.core.cache import Cache
from app.core.errors import DataUnavailable
from app.db import Database
from app.registry.registry import NATIONAL_CODE, Dataset, Registry

COLS = ["district", "date", "value"]
log = logging.getLogger("niwis.data")


class DataAccess:
    def __init__(self, db: Database, registry: Registry, cache: Cache, settings: Settings) -> None:
        self.db, self.registry, self.cache, self.settings = db, registry, cache, settings

    # ---------------------------------------------------------------- helpers

    def source(self, var: str) -> tuple[Dataset, str]:
        src = self.registry.canonical_source(var)
        if src is None:
            reasons = self.registry.withheld_reasons(var)
            label = self.registry.variables[var].label if var in self.registry.variables else var
            raise DataUnavailable(
                f"{label} is not available" + (f": {reasons[0].split(': ', 1)[-1]}" if reasons else " in the database"),
                missing=[var],
            )
        return src

    def table_of(self, var: str) -> str:
        return self.source(var)[0].table

    async def spellings(self, ds: Dataset) -> dict[str, tuple[str, ...]]:
        """MDB code → every spelling of that district found in the table's location column (cached a day).

        SQL filters compare names exactly, but tables differ in case ("UMgungundlovu", "Pixley Ka Seme" in 9_1) and in
        old names; discovering the table's own spellings keeps every district in scope. Indexed tables are read with
        an index skip-scan (one probe per district), others with one DISTINCT scan."""
        if ds.national:
            return {NATIONAL_CODE: (NATIONAL_CODE,)}
        loc = ds.location_sql()

        async def load() -> dict[str, tuple[str, ...]]:
            if ds.sub_district:  # every local-municipality spelling, grouped by its district
                out_lm: dict[str, set[str]] = {}
                for r in await self.db.fetch(f"SELECT DISTINCT {loc} FROM {ds.fq} WHERE {loc} IS NOT NULL"):
                    code = municipalities.district_of(str(r[0]))
                    if code is not None:
                        out_lm.setdefault(code, set()).add(str(r[0]))
                return {k: tuple(sorted(v)) for k, v in out_lm.items()}
            indexed = await self.db.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
                "AND a.attnum = i.indkey[0] WHERE i.indrelid = to_regclass($1) AND a.attname = $2)",
                ds.fq,
                ds.location.column,
            )
            sql = (
                f"WITH RECURSIVE k(v) AS ((SELECT {loc} FROM {ds.fq} WHERE {loc} IS NOT NULL ORDER BY {loc} LIMIT 1) "
                f"UNION ALL SELECT (SELECT {loc} FROM {ds.fq} WHERE {loc} > k.v ORDER BY {loc} LIMIT 1) FROM k "
                f"WHERE k.v IS NOT NULL) SELECT v FROM k WHERE v IS NOT NULL"
                if indexed
                else f"SELECT DISTINCT {loc} FROM {ds.fq} WHERE {loc} IS NOT NULL"
            )
            out: dict[str, set[str]] = {}
            for r in await self.db.fetch(sql):
                d = districts.resolve(str(r[0]))
                if d is not None:
                    out.setdefault(d.code, set()).add(str(r[0]))
            return {k: tuple(sorted(v)) for k, v in out.items()}

        res: dict[str, tuple[str, ...]] = await self.cache.get_or_set(f"data:spellings:{ds.table}", 86400, load)
        return res

    async def names(self, ds: Dataset, code: str) -> list[str]:
        """Every spelling of a district to match in `ds`: the known aliases plus those found in the table. A national
        table has one location, "ZA"."""
        if ds.national:
            return [NATIONAL_CODE] if code == NATIONAL_CODE else []
        if ds.sub_district:  # the table's own spellings of the district's local municipalities (no aliases)
            try:
                return list((await self.spellings(ds)).get(code, ()))
            except Exception:
                log.warning("local-municipality discovery failed", extra={"table": ds.table})
                return []
        d = districts.by_code(code)
        if d is None:
            return []
        known = {d.db_name, d.db_name.lower(), *d.aliases}
        try:
            known |= set((await self.spellings(ds)).get(d.code, ()))
        except Exception:  # never fail a request on the discovery query: fall back to the known spellings
            log.warning("district spelling discovery failed", extra={"table": ds.table})
        return sorted(known)

    @staticmethod
    def _frame(rows: list[Any], ds: Dataset | None = None, agg: str | None = None) -> pd.DataFrame:
        """district, date, value. A local-municipality table's rows are mapped to their districts and combined per
        district-period by `agg` (sum / mean; the registry column's `agg`, mean by default)."""
        df = pd.DataFrame([tuple(r) for r in rows], columns=COLS)
        if df.empty:
            return df
        sub = ds is not None and ds.sub_district
        codes = {
            n: NATIONAL_CODE
            if n == NATIONAL_CODE
            else municipalities.district_of(n)
            if sub
            else (d.code if (d := districts.resolve(n)) else None)
            for n in df["district"].unique()
        }
        df["district"] = df["district"].map(codes)
        df = df.dropna(subset=["district"])
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce").astype(float)
        if sub:
            df = df.dropna(subset=["value"]).groupby(["district", "date"], as_index=False)["value"].agg(agg or "mean")
        return df.reset_index(drop=True)

    async def _cached(
        self, key: str, sql: str, *args: Any, ds: Dataset | None = None, agg: str | None = None
    ) -> pd.DataFrame:
        async def load() -> pd.DataFrame:
            return self._frame(await self.db.fetch(sql, *args), ds=ds, agg=agg)

        df: pd.DataFrame = await self.cache.get_or_set(key, self.settings.cache_ttl_long_series_s, load)
        return df

    # ---------------------------------------------------------------- loaders

    async def monthly(self, var: str) -> pd.DataFrame:
        """One value per district-month (whole record)."""
        ds, col = self.source(var)
        c, loc, d = ds.column_sql(col), ds.location_sql(), ds.date_sql()
        if ds.time.resolution == "monthly" and ds.columns[col].repeats is None and not ds.duplicate_rows:
            sql = f"SELECT {loc}, {d}, {c} FROM {ds.fq} WHERE {ds.valid_sql(col)}"
        elif ds.time.resolution == "monthly" or ds.columns[col].repeats == "monthly":  # one value per month
            sql = (
                f"SELECT {loc}, date_trunc('month', {d})::date AS m, max({c}) FROM {ds.fq} "
                f"WHERE {ds.valid_sql(col)} GROUP BY 1, 2"
            )
        else:
            raise ValueError(f"{var} is not a monthly variable")
        return await self._cached(f"data:monthly:{var}", sql, ds=ds, agg=ds.columns[col].agg)

    async def annual(self, var: str) -> pd.DataFrame:
        """One value per district-year (annual tables, or annual values repeated on daily rows)."""
        ds, col = self.source(var)
        c, loc, d = ds.column_sql(col), ds.location_sql(), ds.date_sql()
        if ds.time.resolution == "annual" and not ds.duplicate_rows:
            sql = f"SELECT {loc}, {d}, {c} FROM {ds.fq} WHERE {ds.valid_sql(col)}"
        elif ds.time.resolution == "annual" or ds.columns[col].repeats == "annual":  # one value per year
            sql = (
                f"SELECT {loc}, date_trunc('year', {d})::date, max({c}) FROM {ds.fq} "
                f"WHERE {ds.valid_sql(col)} GROUP BY 1, 2"
            )
        else:
            raise ValueError(f"{var} is not an annual variable")
        return await self._cached(f"data:annual:{var}", sql, ds=ds, agg=ds.columns[col].agg)

    async def daily_window_stats(
        self, var: str, start: dt.date, end: dt.date, thresholds: tuple[float, ...] = ()
    ) -> pd.DataFrame:
        """Per district over [start, end] from a daily variable, aggregated in SQL (52 rows, not 52 × days).

        Columns: district, sum, days, max, max_date, last_ge_<t>, ge_<t>…  (last_ge = last date with value ≥ t).
        """
        ds, col = self.source(var)
        c, loc, d = ds.column_sql(col), ds.location_sql(), ds.date_sql()
        extra = "".join(
            f", count(*) FILTER (WHERE {c} >= {float(t)!r}), max({d}) FILTER (WHERE {c} >= {float(t)!r})"
            for t in thresholds
        )
        sql = (
            f"SELECT {loc}, sum({c}), count({c}), max({c}), (array_agg({d} ORDER BY {c} DESC NULLS LAST))[1]{extra} "
            f"FROM {ds.fq} WHERE {ds.range_sql()} AND {ds.valid_sql(col)} GROUP BY 1"
        )
        names = ["district", "sum", "days", "max", "max_date"]
        for t in thresholds:
            names += [f"ge_{t:g}", f"last_ge_{t:g}"]

        async def load() -> pd.DataFrame:
            rows = await self.db.fetch(sql, *ds.range_args(start, end))
            df = pd.DataFrame([tuple(r) for r in rows], columns=names)
            codes = {n: (x.code if (x := districts.resolve(n)) else None) for n in df["district"].unique()}
            df["district"] = df["district"].map(codes)
            df = df.dropna(subset=["district"]).set_index("district")
            for n in names[1:]:
                if n.startswith(("last_", "max_date")):
                    df[n] = pd.to_datetime(df[n])
                else:
                    df[n] = pd.to_numeric(df[n]).astype(float)
            return df

        key = f"data:dwstats:{var}:{start}:{end}:{','.join(map(str, thresholds))}"
        out: pd.DataFrame = await self.cache.get_or_set(key, self.settings.cache_ttl_summary_s, load)
        return out

    async def daily_scope_sum(self, var: str, start: dt.date, end: dt.date, factors: dict[str, float]) -> pd.Series:
        """Σ factorᵢ · valueᵢ per day over the districts in `factors` (MDB code → factor), computed in SQL.

        Factors are bind parameters (never string-built). Every spelling of a district maps to the same factor; a day is
        returned only when all districts in `factors` have a value.
        """
        ds, col = self.source(var)
        c, loc, d = ds.column_sql(col), ds.location_sql(), ds.date_sql()
        names: list[str] = []
        facs: list[float] = []
        for code, f in factors.items():
            for n in await self.names(ds, code):
                names.append(n)
                facs.append(float(f))
        sql = (
            f"SELECT {d} AS day, sum({c} * w.f) AS v, count(DISTINCT {loc}) AS n FROM {ds.fq} "
            f"JOIN unnest($3::text[], $4::float8[]) AS w(name, f) ON w.name = {loc} "
            f"WHERE {ds.range_sql()} AND {ds.valid_sql(col)} GROUP BY 1"
        )
        need = len(factors)
        key = f"data:dscope:{var}:{start}:{end}:{hash(tuple(sorted(factors.items())))}"

        async def load() -> pd.Series:
            rows = await self.db.fetch(sql, *ds.range_args(start, end), names, facs)
            vals = {pd.Timestamp(r["day"]): float(r["v"]) for r in rows if r["n"] == need}
            s = pd.Series(list(vals.values()), index=pd.DatetimeIndex(list(vals.keys())), dtype=float)
            return s.sort_index()

        out: pd.Series = await self.cache.get_or_set(key, self.settings.cache_ttl_long_series_s, load)
        return out

    async def daily_exceedances(
        self, var: str, start: dt.date, end: dt.date, thresholds: dict[str, float]
    ) -> pd.DataFrame:
        """Days in [start, end] where a district's value exceeds its threshold (MDB code → threshold), filtered in SQL.

        Only exceedance rows travel and are cached, so arbitrary windows cannot fill memory with daily slices.
        """
        ds, col = self.source(var)
        c, loc, d = ds.column_sql(col), ds.location_sql(), ds.date_sql()
        names: list[str] = []
        thr: list[float] = []
        for code, t in thresholds.items():
            for n in await self.names(ds, code):
                names.append(n)
                thr.append(float(t))
        sql = (
            f"SELECT {loc}, {d}, {c} FROM {ds.fq} JOIN unnest($3::text[], $4::float8[]) AS t(name, thr) "
            f"ON t.name = {loc} WHERE {ds.range_sql()} AND {ds.valid_sql(col)} AND {c} > t.thr "
            f"ORDER BY {c} DESC LIMIT 5000"
        )
        key = f"data:exceed:{var}:{start}:{end}:{hash(tuple(sorted(thresholds.items())))}"
        return await self._cached(key, sql, *ds.range_args(start, end), names, thr)

    async def daily_percentile(self, var: str, start: dt.date, end: dt.date, q: float) -> pd.Series:
        """Per-district percentile `q` (0–1) of a daily variable over [start, end], computed in SQL.

        One statement per district, at most `db_pool_max − 1` at a time: a single grouped query sorts the whole daily
        table (500 000 rows, 21 s on Supabase — past the statement timeout), one district reads ~9 500 rows through
        the (district, date) index in 1–2 s."""
        ds, col = self.source(var)
        c, loc = ds.column_sql(col), ds.location_sql()
        sql = (
            f"SELECT percentile_cont($3) WITHIN GROUP (ORDER BY {c}) FROM {ds.fq} "
            f"WHERE {ds.range_sql()} AND {ds.valid_sql(col)} AND {loc} = ANY($4::text[])"
        )
        sem = asyncio.Semaphore(max(1, self.settings.db_pool_max - 1))

        async def one(d: districts.District) -> tuple[str, float | None]:
            names = await self.names(ds, d.code)
            async with sem:
                v = await self.db.fetchval(sql, *ds.range_args(start, end), q, names)
            return d.code, (float(v) if v is not None else None)

        async def load() -> pd.Series:
            found = await asyncio.gather(*(one(d) for d in districts.districts()))
            return pd.Series({k: v for k, v in found if v is not None}, dtype=float)

        # a whole-record percentile (Q10 thresholds) changes only when a year completes: kept a day
        ttl = max(self.settings.cache_ttl_long_series_s, 86400)
        res: pd.Series = await self.cache.get_or_set(f"data:pct:{var}:{start}:{end}:{q}", ttl, load)
        return res

    async def daily_monthly_stats(self, var: str, thresholds: tuple[float, ...]) -> pd.DataFrame:
        """Per district-month from a daily variable: sum, max, days with data, and days >= each threshold.

        Columns: district, date, sum, max, days, ge_<t>…  (computed in SQL over the whole record).
        """
        ds, col = self.source(var)
        c, loc, d = ds.column_sql(col), ds.location_sql(), ds.date_sql()
        counts = ", ".join(f"count(*) FILTER (WHERE {c} >= {float(t)!r})" for t in thresholds)
        sql = (
            f"SELECT {loc}, date_trunc('month', {d})::date, sum({c}), max({c}), count({c})"
            f"{', ' + counts if counts else ''} FROM {ds.fq} WHERE {ds.valid_sql(col)} GROUP BY 1, 2"
        )
        names = ["district", "date", "sum", "max", "days", *[f"ge_{t:g}" for t in thresholds]]

        async def load() -> pd.DataFrame:
            rows = await self.db.fetch(sql)
            df = pd.DataFrame([tuple(r) for r in rows], columns=names)
            codes = {n: (x.code if (x := districts.resolve(n)) else None) for n in df["district"].unique()}
            df["district"] = df["district"].map(codes)
            df = df.dropna(subset=["district"])
            df["date"] = pd.to_datetime(df["date"])
            for n in names[2:]:
                df[n] = pd.to_numeric(df[n]).astype(float)
            return df.reset_index(drop=True)

        key = f"data:dmstats:{var}:{','.join(map(str, thresholds))}"
        out: pd.DataFrame = await self.cache.get_or_set(key, self.settings.cache_ttl_long_series_s, load)
        return out

    async def newest(self, var: str) -> dt.date:
        """Newest date of the canonical table for `var` (cached via the monthly/daily frames' own queries)."""
        ds, _ = self.source(var)
        d = ds.date_sql()

        async def load() -> dt.date:
            v = await self.db.fetchval(f"SELECT max({d}) FROM {ds.fq} WHERE {ds.rows_sql()}")
            return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))

        out: dt.date = await self.cache.get_or_set(f"data:newest:{ds.table}", self.settings.cache_ttl_meta_s, load)
        return out
