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

Rules, checked on every response (`app/core/response_checks.py`; 2026-10-15, BACKEND_ISSUES #5 / #6):

- A field listed in `unavailable` is `null` (all-null for a series / column). Field paths: `kpis.<id>`,
  `series.<key>`, `rows.<column>` (that cell in every row), `rows.<rowId>` (every non-text cell of that row),
  `rows.<rowId>.<key>`, `values` (every map / matrix value).
- Conversely, a series, KPI, table column or map that is entirely `null` has an `unavailable` entry (KPIs also
  `unavailableReason`) with a specific reason computed from the data, e.g. "The number of drought months (SPI-3 ≤
  −1.5) was 0 in 2020 (the base year) in this scope (lowest area-weighted SPI-3 that year: -1.06), so a % change
  from 2020 is undefined". A partially null series has no entry.
- No non-null value is dated before the first or after the last record of its source tables (time-series x values,
  `YYYY` / `YYYY–YYYY` category and series labels, map `date`, `date` columns, `asOf`).
- A missing value never gets a class: no "No drought" (`None` class) pill, status or map class for a missing SPI,
  and no `0` area in drought for a province without data.

## 4. Provenance on every response

Optional fields added to every top-level response (`KpiSet`, `SeriesSet`, `CategorySet`, `TableSet`, `MatrixSet`,
`MapLayer`, `EntityList`):

| Field | Type | Meaning |
| --- | --- | --- |
| `asOf` | string (ISO date) | Date of the newest record actually used. Already in `KpiSet` / `SeriesSet`; **added** to `CategorySet`, `TableSet`, `MatrixSet`, `MapLayer`, `EntityDetail`. Monthly data are stamped `YYYY-MM-01`. |
| `source` | string[] | Database tables the values came from. |
| `isStale` | boolean | `true` when the value's period **ended** more than **90 days** (configurable: `STALE_AFTER_DAYS`) before the newest date in the database. The latest complete period is never stale (with data to 2026-09-06: August 2026, and the year 2025). Changed 2026-10-15 (BACKEND_ISSUES #7): it used to be measured from the stamped date, so annual values for the last complete year were flagged stale. |
| `periodEnd` | string (ISO date) | **Added 2026-10-15.** Last day of the `asOf` period for monthly (`YYYY-MM-31`) and annual (`YYYY-12-31`) values; absent for daily values. `asOf` keeps the period-start stamp. |
| `components` | `{key, asOf, source, isStale}[]` | Per-component dates for panels that combine sources (drought cascade, headline drought class, mixed KPI sets). |
| `baseline` | string | Reference period of anomalies / normals, e.g. `"2000–2026 monthly mean"`. |
| `baselineNote` | string | `"Baseline shorter than the WMO 30-year standard (1981–2010 data not available)."` |
| `aggregation` | `"district"` \| `"district-area-weighted"` \| `"district-overlap"` | How values were aggregated to the requested scope. |
| `unavailable` | `{field, reason}[]` | See §3. |

Per value: `Kpi` gains optional `asOf`, `source`, `isStale`, `periodEnd` (each KPI may come from a different table,
e.g. SPI to 2026-09, VCI to 2025-12); `components[]` entries carry `periodEnd` too. `EntityDetail.fields[]` gains optional `asOf`, `isStale`. Table rows may carry an `asOf`
cell when rows have different dates.

**Baseline labels.** Panel titles in the contract that say "1981–2010" are served with the real baseline in `baseline`
and a `note`; the backend does not pretend the data cover 1981–2010.

## 5. Errors

| Status | Body | When |
| --- | --- | --- |
| 400 | `{"error", "code": "BAD_REQUEST", "issues": [...]}` | Bad query parameter (FastAPI's 422 is mapped to 400); unknown selector value (§10): `issues[0] = {param, value, allowed}` |
| 404 | `{"error", "code"?: "NOT_FOUND", "issues"?}` | Unknown module, metric, layer, entity or reference list |
| 429 | `{"error": "Too many requests", "code": "RATE_LIMITED"}` + `Retry-After` | Per-IP rate limit, default **600/min** (was 120; `RATE_LIMIT_PER_MINUTE`). `/api/v1/health` and `RATE_LIMIT_TRUSTED_CIDRS` networks are exempt; `X-Forwarded-For` is used only with `TRUST_PROXY_HEADERS=true` |
| 501 | `DATA_UNAVAILABLE` body (§3) | Data not in the database |
| 500 | `{"error": "Internal server error", "requestId"}` | Unexpected error; details only in server logs |

`code` is an optional addition to the contract's `{error, issues?}` body. Every response carries an `X-Request-Id`
header.

## 6. Endpoints

Not implemented by this backend yet (404): every module other than the five listed in §7, `GET /api/v1/alerts`
and `GET /api/v1/search`.

New:

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

**Climate (live).** Annual indicators are for the last full year (currently 2025); trends are `null` with fewer
than 8 years. `evaporation` pan / VPD series and `signal-chain` severity are `null` + `unavailable`; projections,
irrigation, risk index, vulnerability and forecast-summary / risk-outlook → 501. `/climate/forecast` → 404 (no keys).
`seasonal-change`: a period is computed only when the data cover all of it, so `p0` "1981–2000" is `null` +
`unavailable` (it used to hold the year 2000 alone); `var=rainfall` returns mean seasonal totals in mm from `4_3`
(it used to return temperatures). `indicator-trends` with a `scenario` → 501 (no projections; without `scenario`, the
observed series as before).

**Vegetation (live).** All indices come from `satellite_remote_sensing` (same values as the drought page) and
are dated 2025-12 (`isStale: true`). `indices?set=` returns the requested indices (default NDVI, EVI, SAVI, MSAVI2;
VCI / VHI / TCI / NDWI / LST / SM live; EVI / SAVI / MSAVI2 / VPI / ASI / SVI / NDII `null` + `unavailable`); maps `evi`,
`savi`, `msavi2`, `vpi`, `asi`, `svi`, `ndii`, the land-cover breakdown and all forecasts → 501.

**Runoff (live).** Modelled, not gauged: entities are districts (`image: "river"`); flows and volumes are derived
(depth × real area). `peakFlow`, `stationsHighFlow`, `stationsLowFlow` KPIs are `null` + `unavailable`; `events`
severity `null` + `unavailable`; station maps, by-province station counts, catchment runoff and forecasts → 501.
`flood-frequency` uses `xType: "value"`, `xLog: true`, `xUnit: "years"` (return period).

## 8. Derived values

| Value | Derivation |
| --- | --- |
| Runoff volume (Mm³) | runoff depth (mm) × **real district area** (km²) / 1000. Labelled derived. The table's own volumes use a constant 6 500 km² for every district and are not served. |
| Mean daily flow (m³/s) | derived volume / 86 400 s. Peak flows from the table (scaled to 6 500 km²) are withheld. |
| Province / national / WMA values | Area-weighted from districts (§2); never plain means. |

## 9. Cascade tables (2026-10-15, BACKEND_ISSUES #2, #3)

`drought/table?metric=cascade` and `climate/table?metric=signal-chain` rows carry the contract's cascade keys **in
addition to** their own, and the contract's columns are added next to the existing ones:

| Key | Drought cascade | Climate signal chain |
| --- | --- | --- |
| `stage` | stage label (as before) | = `signal` |
| `metric` | `spi`, `vhi`, `storagePct`, `settlementsAtRisk` | `tmean`, `et0`, `rainfall`, `runoff` |
| `value` | **text** with unit: `SPI-6 -1.23`, `VHI 45 %`, `63.6 % FSC` | = `change` (text) |
| `valueNum` | the number (**new**; it was `value`) | — |
| `cls` | class id on `scale`: drought `D0`…`D4` / `No drought`, VCI classes (`Healthy` …), storage bands (`low` …) | `null` + `unavailable` |
| `scale` | `drought`, `vci`, `storage`, `risk` (palettes in thresholds.json / the frontend) | `risk` |
| `bar` | 0–100 from the class rank on its scale (least severe 0, most severe 100, evenly spaced: drought None…D4 = 0, 20 … 100; VCI classes and storage bands = 0, 25, 50, 75, 100) | `null` + `unavailable` |

Kept: drought `class` (label), `severity` (% area in drought / under stress, 100 − storage %), `asOf`; climate
`signal`, `change`, `severity` (`null` + `unavailable`). **One retype:** the drought cascade's `value` was the number
and is now the contract's text; the number is `valueNum`. The frontend's `CascadeFlow` adapter accepts both, and
with the contract keys present it can be simplified to read `{stage, metric, value, cls, scale, bar}` only. A stage
without data has `value`, `valueNum`, `cls`, `bar`, `severity` = `null` (it used to report severity 0 %).

## 10. Selectors (2026-10-15, BACKEND_ISSUES #1)

Panel selectors are validated and honoured (they were all ignored). An unknown value → 400 listing the valid ones.

| Endpoint | Selector | Values (default first) |
| --- | --- | --- |
| `rainfall/timeseries?metric=anomaly`, `drought/timeseries?metric=rain-anomaly` | `window` | `1`, `3`, `6`, `12` months summed before comparing with the normal |
| `rainfall/table?metric=extremes` | `kind` | `wettest`, `driest` |
| `rainfall/breakdown?metric=events` | `kind` | `count` (events > 50 mm/day), `max` (highest daily rainfall, mm) |
| `drought/timeseries?metric=spi` | `scale` | `3`, `1`, `6`, `12`, `24` |
| `drought/timeseries?metric=drought-area` | `scale` | `6`, `1`, `3`, `12`, `24` (SPI used for the classes) |
| `drought/table?metric=affected` | `level` | `province`, `catchment` (→ 501) |
| `climate/timeseries?metric=national-trend` | `var` | `temperature` (°C), `rainfall`, `et` (ET₀), `streamflow` (modelled runoff depth) — annual totals in mm |
| `climate/breakdown?metric=seasonal-change` | `var` | `temperature` (°C), `rainfall` (mm) |
| `climate/timeseries?metric=heat-days` | `kind` | `hot` (Tmax > 35 °C), `cold` (frost nights, Tmin < 0 °C) |
| `climate/timeseries?metric=extreme-rain` | `kind` | `r20` (days ≥ 20 mm), `rx1` (Rx1day, mm) |
| `climate/timeseries?metric=indicator-trends` | `scenario` | absent / `observed`; `rcp45`, `rcp85` → 501 |
| `vegetation/timeseries?metric=indices` | `set` | comma-separated: `ndvi`, `vci`, `vhi`, `tci`, `ndwi`, `lst`, `sm` (live); `evi`, `savi`, `msavi2`, `vpi`, `asi`, `svi`, `ndii` (null + `unavailable`); default `ndvi,evi,savi,msavi2`; at most two units |
| `vegetation/timeseries?metric=index-trend` | `index` | as `set`, one value; withheld indices → 501 |
| `vegetation/timeseries?metric=drivers` | `var` | `rain` (mm), `temp` (Tmax °C), `pet` (ET₀, mm/day), `sm` (m³/m³) |
| `runoff/timeseries?metric=drivers` | `var` | `rain`, `aet` (mm, complete months), `sm` |
| `runoff/table?metric=top-stations` | `order` | `highest`, `lowest` |

## 11. Database category charts (2026-10-15) — new endpoints, additions only

The database view: every registered table is a **category**, every column a **variable**, labelled from its own
column name (docs/CATEGORY_CATALOGUE.md). Two new endpoints per module (`rainfall`, `drought`, `climate`,
`vegetation`, `runoff`); nothing existing changes.

| Endpoint | Response | Notes |
| --- | --- | --- |
| `GET /api/v1/{module}/categories` | `CategoryList` (new) | The module's tables (table → module mapping: `category_modules` in `app/registry/datasets.yaml`), each with `id`, `table`, `label`, `spec` / `specTag` (`"§3.1"`), `resolution`, `asOf` / `isStale`, `counts` (live / withheld / duplicate), `groups` (chart groups: `id`, `label`, `unit`, `resolution`, `columns`, `defaultVisible`, `path`) and `variables` (every column: `column`, `label`, `name`, `unit`, `kind`, `resolution`, `status`, `reason`, `duplicateOf`, `group`, `defaultVisible`, `asOf` / `isStale` / `periodEnd`, `source` = `table.column`, `note`, `unitOverride`). |
| `GET /api/v1/{module}/category/{tableId}/{groupId}` | `SeriesSet` | One series per requested column: `vars=col1,col2` (exact column names), `vars=all`, default the group's `defaultVisible`. Global filters as everywhere (`province`, `wma`, `municipality`, `period`, `from`, `to`); `entityId` selects one district. Unknown table / group → 404 (lists the valid ids); unknown column → 400 (lists the group's columns). |

Additions to `Series` (all optional, set on category series): `unit` (the column's unit; absent for unitless
indices), `asOf`, `isStale`, `periodEnd`, `source` (`["table.column"]`), `resolution` (`daily` / `monthly` /
`annual`), `status` (`live` / `withheld`). `key` = the exact column name, `name` = the catalogue label.

Rules the frontend can rely on:
- **Axis rule:** a group never mixes units, so a category chart always has **one** axis (`axes[0].unit` = the
  group's unit; `index` for unitless SPI/SPEI/SSI/SGI/RAI/SVI or NDVI/NDWI/… groups, which are separate groups).
- **Withheld** columns are all-null series with an `unavailable` entry (`series.<column>`, the reason). Duplicates are
  not requestable (they are listed in `/categories` with `duplicateOf`).
- **Aggregation** (`aggregation` as elsewhere): area-weighted district mean with ≥ 95 % area coverage; volumes, flows
  and areas (Mm³, m³/s, m³/day, m³/year, km²) are summed over the scope's districts (all districts needed).
- **Periods:** monthly groups show whole months ending with the latest complete month the table has; annual groups
  the whole record (or `from` / `to`, `5Y`, `10Y`); incomplete months / years are never served. Daily groups longer
  than 2 000 points are downsampled to monthly means (`note` says so).

## 12. Spec-audit fixes (2026-10-15)

Behaviour and additions from the audit of the live dashboard against the spec (evidence in `PROGRESS.md`). Nothing is
renamed or removed unless stated.

### 12.1 `entityId` selects the district (B1)

Entity datasets now describe the `entityId` district, whatever the page scope (`province`, `wma`, `municipality`):
`runoff/timeseries?metric=discharge|fdc|flood-frequency|seasonal`, `runoff/table?metric=statistics`, every
`/{module}/category/…` chart, and (as before) `rainfall/timeseries?metric=station`. They used to return the scope
aggregate (national, or another area with `province=WC`). With `entityId` the response has
`aggregation: "district"` and the title ends with the district name. Without `entityId` nothing changes. An unknown
code → 404 (`issues[0].param = "entityId"`).

### 12.2 Decimals on every numeric table column (B2)

Every `number` / `delta` / `bar` column now has `decimals` (the frontend rounds to `decimals ?? 0`, which showed the
runoff coefficient 0.198 as "0"). Tables whose rows hold different variables also carry, per row, `unit`,
`decimals` and (runoff) `trendDecimals` (additions; extra row keys are not columns). The column's `decimals` is the
largest any row needs, so nothing rounds away before the frontend reads the row values:
`runoff/table?metric=statistics` — runoff (mm) 1, coefficient 3, trends 2 / 4 (columns 3 / 4);
`rainfall/table?metric=statistics` — 0–1 per row (columns 1).

### 12.3 Drought frequency = area in spec classes D2–D4 (B3)

`climate/timeseries?metric=drought-frequency` counted months where the **national-mean** SPI-3 was ≤ −1.5 (not a spec
threshold): averaging cancels out regional drought, so it showed 0 months in 26 years, 2015–16 included. Now each
district's SPI-3 is classified with the spec §3.4 thresholds and the areas are added up per month, then averaged over
the full year. Series: `d2`, `d3`, `d4` (new; stacked bars, % of the scope's area, axis 0), `trend` (Sen's slope of the
D2–D4 share, axis 0), `months` (**redefined**: the number of district-months in D2 or worse, now on axis 1, unit
`district-months`). Axes: `%` and `district-months`. `indicator-trends` → `drought` is now the % change of the
yearly D2–D4 area share vs 2020 (was the count of months with mean SPI-3 ≤ −1.5).

### 12.4 Drought class from areas, not from averaged SPI (B4)

The Drought Class KPI (`drought/summary` → `class`) took the class of the **area-weighted mean** SPI-6, which hides
regional drought (KZN 85 % in D1 while the national KPI said "No drought" — and it still says so: 8 % of the country
is in D1–D4). Now each district's SPI-6 is classified (spec §3.4) and the headline is the **worst class that, together
with all worse classes, covers ≥ 20 % of the scope's area** (`DROUGHT_CLASS_AREA_PCT`, default 20; cumulative, so
15 % D3 + 10 % D4 → D3). `value` = class rank (unchanged), `text` / `status` = that class, `sublabel` gives the
sub-value: "85 % of area in D1–D4 · worst class on ≥ 20 % of the area (SPI-6, Aug 2026)". Null when districts with
data cover < 95 % of the area. The same rule now gives the class per province on `drought/map?layer=province-class`
and in `drought/table?metric=affected` (`class`), so the pages agree; the averaged indices stay, labelled
"(area-weighted mean)": the SPI-3 / SPI-6 / SPEI-12 KPIs on aggregated scopes and the affected table's `spi6` column.

### 12.5 Temperature-type changes in their own unit (B5)

A % change of a temperature depends on where zero is, so it is meaningless. `vegetation/breakdown?metric=drivers`:
rainfall and ET₀ stay % changes; mean temperature and LST are absolute changes in **°C**, growing degree days (verified
degree-day sums) in **°C·days**. New `units` (one per category, addition) and the unit in each category name
(`"Mean temperature (°C)"`); `unit` is `"mixed"` (it was `"%"`). `climate/timeseries?metric=indicator-trends`: the
`temp` series is the change in °C on a second axis (`axes[1].unit = "°C"`, `axis: 1`); it was 10 × °C on the % axis.

### 12.6 No developer notes in responses (B6)

`note`, `reason` and error texts that addressed developers ("Contract label '1981–2010' → 2000–2026 …", documentation
paths, decision ids) are rewritten for users, e.g. "Normal: 2000–2026 mean (data start in 2000)". Wording only; no
keys change. `tests/test_user_facing_text.py` scans every response.

### 12.7 Drought cascade: the spec's 7 stages (B7)

`drought/table?metric=cascade` returns the 7 stages of spec §3 / §25.1, in order: precipitation deficit → soil
moisture → vegetation → streamflow → reservoir → groundwater → socio-economic. Each row adds `stageId` and `order`
(1–7); the four original row ids are kept (`meteorological` = precipitation, `agricultural` = vegetation,
`hydrological` = reservoir, `socio-economic`). New rows: `soil-moisture` (surface soil-moisture anomaly % vs
2001–2025; `cls` / `bar` / `class` / `severity` null with a reason: no agreed classes), `streamflow` (null: no gauged
discharge), `groundwater` (SGI, `scale: "status"`, `cls` = borehole status Normal / Watch / Low / Critical from
thresholds.ts, `severity` = % of area at Low or worse). The reservoir row is null with the plausibility reason
(§15 of DATA_GAPS). The precipitation `cls` / `class` now follow the area rule of the Drought Class KPI (12.4) and its
`severity` is the % of area in D1–D4.

### 12.8 One SSI rule (B8)

SSI is computed by the API from the scope's **modelled monthly runoff depth** (`runoff_variables_master`, gamma fit
per calendar month, 2000–last full year) on **both** pages, labelled "SSI (modelled runoff)": `drought/summary` →
`ssi3` (was always null) and `runoff/summary` → `ssi6`, `runoff/timeseries?metric=ssi`. The database's own
`ssi_dimensionless` stays withheld everywhere (computed from the placeholder discharge). A null SSI says why
(e.g. fewer than 10 calibration years). Labels change; keys do not.

### 12.9 Flood-frequency return periods (B9)

`runoff/timeseries?metric=flood-frequency`: the fit and confidence band are given at the spec §7.2 return periods
**2, 5, 10, 20, 50, 100 years** (1.25 and 25 dropped).

### 12.10 One long-term period (B10)

`runoff/timeseries?metric=fdc`: the long-term curve (`lt`) uses all days 2000–(last full year), i.e. 2000–2025, like
every other long-term value; it used 2011–2020. The series name and `note` give the period.

### 12.11 Each page's seasons from its own table (B11)

`rainfall/breakdown?metric=seasonal`: series `jfm`, `amj`, `jas`, `ond` (spec §6.1) read from `6.1`'s stored seasonal
totals (each the sum of three calendar months of the same year); it served DJF / MAM / JJA / SON (`djf`, `mam`,
`jja`, `son` keys are gone from this panel). The climate page keeps `4_3`'s DJF / MAM / JJA / SON (spec §4.3; DJF =
January + February + December of the same calendar year, as stored).

### 12.12 NDVI trend classes from the data (B12)

`vegetation/map?layer=ndvi-trend` adds `legend` (addition to `MapLayer`): five classes `{from, to, color, label}`
(open ends null), symmetric around 0, with breaks ±a / ±b at the 40 % / 80 % quantiles of |trend| over all 52 districts
(2 significant figures; the same for every scope), using the `ndviTrend` colours. The palette's fixed ±0.05 / ±0.1 per
decade put all 52 districts in the middle class; on Supabase the breaks are ±0.013 / ±0.019 per decade and the
districts fall 21 / 19 / 12 into the stable / greening / strong greening classes (none browning). When `legend` is
present it replaces the palette's classes.

### 12.13 Sources: each column's real resolution (B14)

`rainfall/table?metric=sources` gives each source column its own resolution (`resolution`, `cadence`): values the
database repeats on every daily row count once per period. TAMSAT is now **monthly** ("TAMSAT rainfall estimate (mm
per dekad, one value per month)"); it was listed as daily. The spec (§6.1) expects a dekadal product; the database holds
one value per month (DATA_GAPS §15).

