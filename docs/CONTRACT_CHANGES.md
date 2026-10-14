# Contract changes

> Differences between this backend and `reference/API_CONTRACT.md` (generated 2026-10-09). **Every change is a
> backwards-compatible addition**: new optional fields, new endpoints, new error codes. No existing field is renamed,
> retyped or removed, and zod `.optional()` fields are omitted (never sent as `null`). The Pydantic models in
> `app/schemas/contract.py` enforce this: `tests/test_contract_conformance.py` checks every model's fields against
> the zod schemas and validates every example payload in the contract.

## 1. Districts instead of stations and grid cells (decision D1)

All NIWIS tables are keyed by the **52 district / metro municipalities**; there are no stations, coordinates or grid
cells in the database.

| Contract item | What the backend returns |
| --- | --- |
| `GET /{module}/entities` (rainfall: 213 stations, runoff: 1 842 stations) | 52 entities with `type: "district"`, `id` = MDB code (`DC44`, `CPT`, …), `province` set; no `lat`/`lon` |
| `GET /{module}/entities/{id}` | Card for the district; `image: "municipality"` |
| `MapLayer` with `kind: "grid"` | `kind: "choropleth"`, `level: "municipality"`, `values` keyed by **district MDB code**, plus `geometry: "districts"` (new optional field naming the boundary set) |
| `MapLayer` with `kind: "points"` (stations, dams, boreholes) | 501 `DATA_UNAVAILABLE` (no coordinates) |
| Province-level layers (`province-class` …) | `kind: "choropleth"`, `level: "province"`, keys = province codes (unchanged meaning) |

**Frontend impact:** the map needs district polygons keyed by MDB code. The frontend's `public/geo/municipalities.json`
has 213 *local* municipalities (ids `LM001…`, no district code), so it cannot draw these layers. Copy the backend's
`reference/geo/districts.json` (52 features, `properties.id` = MDB code, 0.8 MB) into `public/geo/` — it is the same
boundary set the backend uses for area weights.

## 2. Filters

| Param | Behaviour |
| --- | --- |
| `municipality` | District / metro MDB codes (`/reference/municipalities` lists them). A **local** municipality code (e.g. `EC101`) → `400` with a message pointing to the parent district. |
| `province` | Area-weighted aggregate of the province's districts. |
| `wma` | Aggregate of districts weighted by their **area of overlap** with the WMA. Approximate: responses carry `"aggregation": "district-overlap"`. |
| `catchment` | `501 DATA_UNAVAILABLE` — districts are far coarser than catchments. |
| `period`, `from`, `to`, `compare`, `entityId` | As in the contract. Bad values → `400`. |

`GET /reference/municipalities` returns the 52 districts (`parent` = province). `GET /reference/catchments` returns
`501 DATA_UNAVAILABLE`.

## 3. Unavailable-data convention

The frontend shows any panel that hits either form below as **"Sample"**.

**Whole endpoint / dataset unavailable → HTTP 501**

```json
{
  "error": "Forecasts are not available yet: no forecast models or tables exist.",
  "code": "DATA_UNAVAILABLE",
  "detail": "Forecasts are not available yet: no forecast models or tables exist.",
  "missing": ["forecast:rainfall"]
}
```

`error` keeps the contract's error shape; `detail` repeats it; `missing` lists what the database lacks.
Used for: all `forecast` endpoints, points layers without coordinates, datasets whose only source is withheld
(e.g. `vegetation` EVI/SAVI/MSAVI2/VPI/ASI/SVI/NDII maps), catchment filtering.

**Individual field unavailable inside a live response → `null` + `unavailable` entry**

Only where the contract allows `null` (`Kpi.value`, `CategorySet` values, table cells, map values):

```json
{
  "kpis": [
    {"id": "stations", "metric": "activeStations", "label": "Active Rainfall Stations", "value": null,
     "unavailableReason": "No station data in the database"}
  ],
  "unavailable": [{"field": "kpis.stations", "reason": "No station data in the database"}]
}
```

`unavailable` (top level, optional) and `unavailableReason` (on a KPI, optional) are additions.

## 4. Provenance on every response

Optional fields added to every top-level response (`KpiSet`, `SeriesSet`, `CategorySet`, `TableSet`, `MatrixSet`,
`MapLayer`, `EntityList`):

| Field | Type | Meaning |
| --- | --- | --- |
| `asOf` | string (ISO date) | Date of the newest record actually used. Already in `KpiSet` / `SeriesSet`; **added** to `CategorySet`, `TableSet`, `MatrixSet`, `MapLayer`, `EntityDetail`. Monthly data are stamped `YYYY-MM-01`. |
| `source` | string[] | Database tables the values came from. |
| `isStale` | boolean | `true` when `asOf` is more than **90 days** (configurable: `STALE_AFTER_DAYS`) older than the newest date in the database. |
| `components` | `{key, asOf, source, isStale}[]` | Per-component dates for panels that combine sources (drought cascade, headline drought class, mixed KPI sets). |
| `baseline` | string | Reference period of anomalies / normals, e.g. `"2000–2026 monthly mean"`. |
| `baselineNote` | string | `"Baseline shorter than the WMO 30-year standard (1981–2010 data not available)."` |
| `aggregation` | `"district"` \| `"district-area-weighted"` \| `"district-overlap"` | How values were aggregated to the requested scope. |
| `unavailable` | `{field, reason}[]` | See §3. |

Per value: `Kpi` gains optional `asOf`, `source`, `isStale` (each KPI may come from a different table, e.g. SPI to
2026-09, VCI to 2025-12). `EntityDetail.fields[]` gains optional `asOf`, `isStale`. Table rows may carry an `asOf`
cell when rows have different dates.

**Baseline labels.** Panel titles in the contract that say "1981–2010" are served with the real baseline in `baseline`
and a `note`; the backend does not pretend the data cover 1981–2010.

## 5. Errors

| Status | Body | When |
| --- | --- | --- |
| 400 | `{"error", "code": "BAD_REQUEST", "issues": [...]}` | Bad query parameter (FastAPI's 422 is mapped to 400) |
| 404 | `{"error", "code"?: "NOT_FOUND", "issues"?}` | Unknown module, metric, layer, entity or reference list |
| 429 | `{"error": "Too many requests", "code": "RATE_LIMITED"}` + `Retry-After` | Per-IP rate limit (default 120/min) |
| 501 | `DATA_UNAVAILABLE` body (§3) | Data not in the database |
| 500 | `{"error": "Internal server error", "requestId"}` | Unexpected error; details only in server logs |

`code` is an optional addition to the contract's `{error, issues?}` body. Every response carries an `X-Request-Id`
header.

## 6. New endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/health` | Database reachability, read-only session check, registry and boundary status, per-module `live` / `unavailable` with reason |
| `GET /api/v1/meta/sources` | Per table: rows, districts, first date, `asOf`, `coveredTo`, `isStale`, completeness %, size, canonical / duplicate / withheld columns, known issues |
| `GET /api/v1/meta/coverage` | Per module roll-up of the above (for the Data Integration and Governance pages) |
| `GET /api/v1/meta/variables` | Every variable: canonical source, duplicates, or why it is unavailable |

## 7. Module notes

**Rainfall (live).** `entities` are the 52 districts (`type: "district"`); `timeseries?metric=station` needs
`entityId` (district MDB code; missing → 400, unknown → 404). SPI / SPEI values are for the latest complete month (the
current month is partial). `activeStations` KPI is `null` with `unavailableReason`; the `sources` table's Stations
column is `null` with an `unavailable` entry. `matrix` has no rainfall datasets (any metric → 404).

**Drought (live).** KPIs from the satellite and hydrology tables (VCI, soil moisture, dam storage, SGI) are dated
2025-12 and `isStale: true`; SPI / SPEI are for the latest complete month. A top-level `isStale` on a response with
`components` is `true` when **any** component is stale. `ssi3` and `settlements` KPIs are `null` + `unavailable`.
`gw-sgi`'s "Mean borehole level" series is all `null` + `unavailable` (withheld). Points layers (`dams`, `boreholes`,
`settlements`), `catchment-class`, restrictions, probabilities, early warning and all forecasts → 501.

## 8. Derived values

| Value | Derivation |
| --- | --- |
| Runoff volume (Mm³) | runoff depth (mm) × **real district area** (km²) / 1000. Labelled derived. The table's own volumes use a constant 6 500 km² for every district and are not served. |
| Mean daily flow (m³/s) | derived volume / 86 400 s. Peak flows from the table (scaled to 6 500 km²) are withheld. |
| Province / national / WMA values | Area-weighted from districts (§2); never plain means. |
