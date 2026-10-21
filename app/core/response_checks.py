"""Invariants every response body must satisfy, checked in the response layer and by the audit tests.

1. `unavailable_violations`: a field listed in `unavailable` is `null` (or all-null). Field paths:
   `kpis.<id>` (the KPI's value), `series.<key>` (every y-value of a series / category series), `rows.<column>`
   (that cell in every row), `rows.<rowId>` (every non-text, non-rank column of that row), `rows.<rowId>.<key>` (one
   cell), `values` (every map value). An entry whose target does not exist is a violation too (typos hide nulls).
2. `unexplained_null_violations`: the converse. A series, KPI, table column or map that is entirely `null` must have
   an `unavailable` entry (or, for a KPI, `unavailableReason` / `text`), so the UI can say why.
3. `coverage_violations`: no non-null value is dated before the first or after the last record of the response's
   source tables (time-series x values, `YYYY` / `YYYY–YYYY` category and series labels, map `date`, `date` columns,
   `asOf` stamps).

Each function returns human-readable violation strings; an empty list means the body passes.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.core.provenance import period_end

Coverage = Mapping[str, tuple[dt.date, dt.date]]  # table → (first record, last record)

YEARS = re.compile(r"^(\d{4})(?:\s*[–-]\s*(\d{4}))?$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _all_null(values: Iterable[Any]) -> bool:
    return all(v is None for v in values)


def _series_values(s: Mapping[str, Any]) -> list[Any]:
    """y-values of a time series (`[x, y]` or `[x, lo, hi]`) or the values of a category series."""
    if "data" in s:
        return [v for point in s["data"] for v in point[1:]]
    return list(s.get("values", []))


def _cell_values(body: Mapping[str, Any]) -> list[Any]:
    """Map values (`{region: value}`) or matrix values (`[[…], …]`)."""
    v = body.get("values")
    if isinstance(v, dict):
        return list(v.values())
    if isinstance(v, list):
        return [x for row in v for x in (row if isinstance(row, list) else [row])]
    return []


def _value_columns(body: Mapping[str, Any]) -> list[str]:
    return [c["key"] for c in body.get("columns", []) if c.get("type") not in ("text", "rank")]


def _row_id(body: Mapping[str, Any], row: Mapping[str, Any]) -> Any:
    return row.get(body.get("rowKey") or "id", row.get("id"))


def _target_values(body: Mapping[str, Any], field: str) -> list[Any] | None:
    """Values the `unavailable` field path points at, or None when the path matches nothing."""
    head, _, rest = field.partition(".")
    if head == "kpis":
        hits = [k.get("value") for k in body.get("kpis", []) if k.get("id") == rest]
        return hits or None
    if head == "series":
        found = [s for s in body.get("series", []) if s.get("key") == rest]
        return [v for s in found for v in _series_values(s)] if found else None
    if head == "values" and not rest:
        return _cell_values(body)
    if head == "fields":  # entity card fields, by label
        hits = [f.get("value") for f in body.get("fields", []) if f.get("label") == rest]
        return hits or None
    if head == "rows":
        rows = body.get("rows", [])
        columns = {c["key"] for c in body.get("columns", [])}
        if rest in columns or any(rest in r for r in rows):
            return [r.get(rest) for r in rows]
        row_id, _, key = rest.partition(".")
        mine = [r for r in rows if str(_row_id(body, r)) == row_id]
        if not mine:
            return None
        if key:
            return [r.get(key) for r in mine]
        return [r.get(k) for r in mine for k in _value_columns(body)]
    return None


def unavailable_violations(body: Mapping[str, Any]) -> list[str]:
    out = []
    for u in body.get("unavailable") or []:
        field = u.get("field", "")
        vals = _target_values(body, field)
        if vals is None:
            out.append(f"unavailable field {field!r} matches nothing in the response")
        elif not _all_null(vals):
            shown = [v for v in vals if v is not None][:4]
            out.append(f"{field!r} is listed as unavailable but has values {shown}")
    return out


def _listed(body: Mapping[str, Any]) -> set[str]:
    return {u.get("field", "") for u in body.get("unavailable") or []}


def unexplained_null_violations(body: Mapping[str, Any]) -> list[str]:
    listed = _listed(body)
    out = []
    for k in body.get("kpis", []) or []:
        if k.get("value") is None and not k.get("text") and f"kpis.{k.get('id')}" not in listed:
            out.append(f"KPI {k.get('id')!r} is null without an unavailable entry")
    for s in body.get("series", []) or []:
        vals = _series_values(s)
        if vals and _all_null(vals) and f"series.{s.get('key')}" not in listed:
            out.append(f"series {s.get('key')!r} is entirely null without an unavailable entry")
    rows = body.get("rows") or []
    if rows:
        for col in body.get("columns", []):
            key = col["key"]
            if _all_null(r.get(key) for r in rows) and f"rows.{key}" not in listed:
                out.append(f"column {key!r} is entirely null without an unavailable entry")
    cells = _cell_values(body)
    if cells and _all_null(cells) and "values" not in listed:
        out.append("map / matrix values are entirely null without an unavailable entry")
    return out


def _date(s: Any) -> dt.date | None:
    if isinstance(s, str) and ISO.match(s):
        try:
            return dt.date.fromisoformat(s)
        except ValueError:
            return None
    return None


def _span(sources: Iterable[str], coverage: Coverage) -> tuple[dt.date, dt.date] | None:
    spans = [coverage[t] for t in sources if t in coverage]
    if not spans:
        return None
    return min(a for a, _ in spans), max(b for _, b in spans)


def coverage_violations(body: Mapping[str, Any], coverage: Coverage) -> list[str]:
    """Non-null values dated outside the first/last record of the response's source tables."""
    span = _span(body.get("source") or [], coverage)
    if span is None:
        return []
    first, last = span
    out: list[str] = []

    def check(d: dt.date | None, what: str, lo: dt.date = first, hi: dt.date = last) -> None:
        if d is not None and not lo <= d <= hi:
            out.append(f"{what} dated {d} is outside the source coverage {lo} – {hi}")

    def check_years(label: str, what: str) -> None:
        m = YEARS.match(label.strip())
        if not m:
            return
        y0 = int(m.group(1))
        y1 = int(m.group(2) or y0)
        if y0 < first.year or y1 > last.year:
            out.append(f"{what} {label!r} reaches outside the source coverage {first.year}–{last.year}")

    if body.get("xType") == "time":
        for s in body.get("series", []):
            res = s.get("resolution")
            for point in s.get("data", []):
                if any(v is not None for v in point[1:]):
                    d = _date(point[0])
                    if d is not None and res in ("monthly", "annual"):
                        # stamped at the start of its period: valid when the period overlaps the coverage (10_1 stores
                        # annual values on 31 Dec, 12.x monthly values on the 25th, 10_2 on the month's last day)
                        if d > last or period_end(d, res) < first:
                            out.append(
                                f"series {s.get('key')!r} value for the period from {d} is outside the "
                                f"source coverage {first} – {last}"
                            )
                        continue
                    check(d, f"series {s.get('key')!r} value")
    if "categories" in body:
        cats = body.get("categories", [])
        for s in body.get("series", []):
            vals = s.get("values", [])
            if not _all_null(vals):
                check_years(str(s.get("name", "")), "category series")
            for cat, v in zip(cats, vals, strict=False):
                if v is not None:
                    check_years(str(cat), "category")
    if isinstance(body.get("values"), dict) and not _all_null(_cell_values(body)):
        check(_date(body.get("date")), "map")
    for col in body.get("columns", []):
        if col.get("type") == "date":
            for r in body.get("rows", []):
                check(_date(r.get(col["key"])), f"table cell {col['key']!r}")
    check(_date(body.get("asOf")), "asOf")
    for k in body.get("kpis", []) or []:
        if k.get("value") is not None and k.get("source"):
            kspan = _span(k["source"], coverage)
            if kspan:
                check(_date(k.get("asOf")), f"KPI {k.get('id')!r} asOf", *kspan)
    return out


def attach_reasons(body: Any, reasons: Mapping[str, str]) -> None:
    """Add `{field, reason}` to `unavailable` for each registered field whose target ended up entirely null.

    Services register a specific reason where a value can become null (`Ctx.explain`); the reason is attached only
    if the series / KPI / column really is all-null in the final body, so partial gaps and filled values never get one.
    """
    if not isinstance(body, dict) or not reasons:
        return
    listed = _listed(body)
    for field, reason in reasons.items():
        vals = _target_values(body, field)
        if field not in listed and vals and _all_null(vals):
            body.setdefault("unavailable", []).append({"field": field, "reason": reason})
            listed.add(field)
            head, _, kid = field.partition(".")
            for k in body.get("kpis", []) if head == "kpis" else []:
                if k.get("id") == kid:
                    k.setdefault("unavailableReason", reason)


def invariant_violations(body: Any, coverage: Coverage | None = None) -> list[str]:
    """Checks that guard against wrong numbers: unavailable-but-valued fields, values outside source coverage."""
    if not isinstance(body, dict):
        return []
    out = unavailable_violations(body)
    if coverage is not None:
        out += coverage_violations(body, coverage)
    return out


def all_violations(body: Any, coverage: Coverage | None = None) -> list[str]:
    """Invariants plus completeness (every all-null element says why)."""
    if not isinstance(body, dict):
        return []
    return invariant_violations(body, coverage) + unexplained_null_violations(body)
