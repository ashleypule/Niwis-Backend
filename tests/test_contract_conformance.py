"""The Pydantic models must match reference/API_CONTRACT.md exactly.

1. Every example payload in the contract validates against the corresponding model.
2. Every zod schema's field names equal the model's aliases, apart from the documented optional additions
   (docs/CONTRACT_CHANGES.md).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.schemas import contract as c

CONTRACT = (Path(__file__).resolve().parent.parent / "reference" / "API_CONTRACT.md").read_text(encoding="utf-8")

PROVENANCE = {"source", "isStale", "periodEnd", "components", "baseline", "baselineNote", "aggregation", "unavailable"}
ADDITIONS: dict[str, set[str]] = {
    "KpiSchema": {"asOf", "source", "isStale", "periodEnd", "unavailableReason"},
    "KpiSetSchema": PROVENANCE,
    "SeriesSetSchema": PROVENANCE,
    "CategorySetSchema": PROVENANCE | {"asOf"},
    "TableSetSchema": PROVENANCE | {"asOf"},
    "MatrixSetSchema": PROVENANCE | {"asOf"},
    "MapLayerSchema": PROVENANCE | {"asOf", "geometry"},
    "EntityListSchema": PROVENANCE,
    "EntityDetailSchema": {"asOf", "source"},
}
MODELS: dict[str, type[BaseModel]] = {
    "ScopeSchema": c.Scope,
    "KpiSchema": c.Kpi,
    "KpiSetSchema": c.KpiSet,
    "SeriesSchema": c.Series,
    "AxisSchema": c.Axis,
    "SeriesSetSchema": c.SeriesSet,
    "CategorySetSchema": c.CategorySet,
    "ColumnSchema": c.Column,
    "TableSetSchema": c.TableSet,
    "MatrixSetSchema": c.MatrixSet,
    "MapPointSchema": c.MapPoint,
    "MapLayerSchema": c.MapLayer,
    "EntitySchema": c.Entity,
    "EntityListSchema": c.EntityList,
    "EntityDetailSchema": c.EntityDetail,
    "AlertSchema": c.Alert,
    "AlertListSchema": c.AlertList,
    "SearchHitSchema": c.SearchHit,
    "SearchResultSchema": c.SearchResult,
    "RefItemSchema": c.RefItem,
    "RefListSchema": c.RefList,
}


def _zod_fields() -> dict[str, set[str]]:
    """Top-level keys of each `export const XSchema = z.object({...})` / `.extend({...})` in the contract."""
    ts = CONTRACT[CONTRACT.index("```ts") : CONTRACT.index("```", CONTRACT.index("```ts") + 5)]
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"export const (\w+Schema) = (?:z\.object|(\w+Schema)\.extend)\(\{\n(.*?)\n\}\)", ts, re.S):
        name, parent, body = m.group(1), m.group(2), m.group(3)
        keys = set(re.findall(r"^  (\w+):", body, re.M))
        if parent:
            keys |= out[parent]
        out[name] = keys
    for m in re.finditer(r"export const (\w+Schema) = z\.object\(\{ (.*?) \}\);", ts):  # one-line objects
        out[m.group(1)] = set(re.findall(r"(\w+): z", m.group(2)))
    return out


def _aliases(model: type[BaseModel]) -> set[str]:
    return {f.alias or name for name, f in model.model_fields.items()}


ZOD = _zod_fields()


def test_parsed_all_schemas() -> None:
    assert set(MODELS) <= set(ZOD), set(MODELS) - set(ZOD)


@pytest.mark.parametrize("schema", sorted(MODELS))
def test_model_fields_match_zod(schema: str) -> None:
    assert _aliases(MODELS[schema]) - ADDITIONS.get(schema, set()) == ZOD[schema]


def _strip_truncation(v: Any) -> Any:
    """Contract examples truncate arrays with strings like "… 14 more"; drop them."""
    if isinstance(v, list):
        return [_strip_truncation(x) for x in v if not (isinstance(x, str) and x.startswith("… "))]
    if isinstance(v, dict):
        return {k: _strip_truncation(x) for k, x in v.items()}
    return v


def _examples() -> list[tuple[str, Any]]:
    sec = CONTRACT[CONTRACT.index("## Example payloads") :]
    return [
        (m.group(1).split()[0], json.loads(m.group(2)))
        for m in re.finditer(r"### (\w+(?: \(\w+\))?)\n\n`GET [^`]+`\n\n```json\n(.*?)\n```", sec, re.S)
    ]


EXAMPLE_MODELS = {"KpiSet": c.KpiSet, "SeriesSet": c.SeriesSet, "TableSet": c.TableSet, "MapLayer": c.MapLayer}


def test_found_examples() -> None:
    assert sorted(name for name, _ in _examples()) == sorted(EXAMPLE_MODELS)


@pytest.mark.parametrize(("name", "payload"), _examples(), ids=[n for n, _ in _examples()])
def test_examples_validate(name: str, payload: Any) -> None:
    model = EXAMPLE_MODELS[name].model_validate(_strip_truncation(payload))
    # Round trip: serialising must reproduce the (stripped) example exactly — no extra keys, no nulls added.
    assert model.dump() == json.loads(json.dumps(_strip_truncation(payload)))


def test_optional_fields_are_omitted_not_null() -> None:
    kpi = c.Kpi(id="x", metric="rainfall", label="Rainfall", value=None)
    assert kpi.dump() == {"id": "x", "metric": "rainfall", "label": "Rainfall", "value": None}
