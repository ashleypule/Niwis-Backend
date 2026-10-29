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
(§15 of DATA_GAPS). Streamflow and groundwater: see 14.8. The precipitation `cls` / `class` now follow the area rule of the Drought Class KPI (12.4) and its
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

## 13. Category-chart review fixes (2026-10-16)

### 13.1 Duplicates per page

A `duplicate` column is hidden on a page only when the column it duplicates is shown on the **same** page (its table is
in that page's `category_modules`). On every other page it is served as a normal **live** column in its own table's
chart groups, with `note` "Same values as <table.column>" and `duplicateOf` kept. Groups (ids, labels, members,
`defaultVisible`) are therefore per page: always take them from the page's `/categories` (`groups[].path`), never
from another page. Example: the Drought page now charts `meteorological_drought_master`'s rainfall and temperature
columns (6.1 and 4_1 are not on that page), but not its SPI (6.2 is). `counts` are per page.

### 13.2 Recommended chart type (BACKEND_ISSUES #8)

Every catalogue variable has `chart` (`bar` for totals and counts, `diverging-bar` for anomalies, `line` for states
and indices, standardised indices such as RAI included); every group has `chart` (the members' common type, `band`
when the group holds a min / max pair, else `mixed`) and, for bands, `band: [min column, max column]` (e.g.
`["Tmin_C", "Tmax_C"]`). Category series carry the column's `chart` and `kind: "bar"` for bar types (was always
`line`); the SeriesSet carries the group's `chart` / `band`.

### 13.3 No null `asOf` (BACKEND_ISSUES #10)

Optional `asOf` / `periodEnd` are omitted when there is no date (e.g. category groups whose columns are all withheld),
never sent as `null`; table cells are unchanged (a cascade row's `asOf` cell is still null when the stage has no data).

### 13.4 Summary series name their column (BACKEND_ISSUES #11)

Series on the module endpoints that plot **one stored column** (as stored, area-weighted, or a monthly / annual mean)
carry `source: ["table.column"]`, the same key category series have: e.g. `drought/timeseries?metric=soil-moisture`
`surface` → `satellite_remote_sensing.surface_soil_moisture`, `rainfall/timeseries?metric=spi-spei`, vegetation
`indices`, climate `temperature-long` / `extremes` / `national-trend` (temperature, rainfall), drivers (mean-type),
the drought SPI / SPEI / NDVI / VCI / VHI series. Derived series (sums, counts from daily values, anomalies, area
shares, fits, bands) have no `source`: use their `name`.

### 13.5 Vegetation classes: spec names (BACKEND_ISSUES #12)

VCI / VHI / TCI classes are **Excellent / Good / Fair / Poor / Very Poor** (spec §5.3; breaks unchanged at 80 / 60 /
40 / 20 %) everywhere a class is sent: vegetation `summary` status, `breakdown?metric=classification` categories,
`table?metric=provinces|hotspots`, and the drought cascade's vegetation stage (`cls`, `class`). They were Very
Healthy / Healthy / Moderate / Stressed / Very Stressed. `reference/thresholds.ts` is refreshed from the frontend.
The frontend's `normalizeVegClasses()` mapping is no longer needed.

### 13.6 Category charts answer from cache (BACKEND_ISSUES #13)

No contract change. Every chart group of every page is pre-loaded at startup for the default request (national,
`period=12M`) and refreshed before the cache expires (`PREWARM_CATEGORIES`, default on; at most `DB_POOL_MAX − 1`
queries at a time, so requests always get a connection): 43 groups in ≈ 20 s on Supabase, then a few ms per request.
All columns of a group are now read in one table scan, so uncached scopes are faster too (`temperature-c` for DC32:
4.2 s → 2.1 s cold).

### 13.7 SGI served May 2002 – Aug 2024 only

SGI repeats its Aug 2024 value in every district from Sep 2024, so it is served for May 2002 – Aug 2024 only (registry
`valid_from` / `valid_until`; a new `valid_until` sits next to `valid_from`):
- `drought/timeseries?metric=gw-sgi` is **200 again** (was 501 in 13.x): the `sgi` series covers the page period
  ending Aug 2024, with `asOf` 2024-08-01, `isStale: true`, `periodEnd` 2024-08-31 and `source`; the response has the
  same `asOf` / `isStale` / `periodEnd` and `note` "Values after Aug 2024 repeat Aug 2024 and are withheld." The
  borehole level stays null + `unavailable`.
- Category view: `sgi_dimensionless` is `live` with `asOf` 2024-08-01, `isStale: true` and the note "Served May 2002 –
  Aug 2024 only: …"; series stop at Aug 2024 (`periodEnd` 2024-08-31) and the SeriesSet `note` says so. A period
  entirely after Aug 2024 (the default 12M) gives a null series whose `unavailable` reason names the cut-off.
- `drought/summary` → `sgi` KPI stays **null** (the cascade groundwater stage shows the `10_1` level anomaly, 14.8), reason
  "Latest valid SGI is Aug 2024 (more than 90 days old)" (they come back once a current SGI exists).

### 13.8 Scoped category requests no longer time out (BACKEND_ISSUES #14)

No contract change. Cause (EXPLAIN ANALYZE on Supabase): scoped annual / monthly groups of daily tables read every daily
row of the scope to get one repeated value per period (seasonal totals, Western Cape: 56 982 rows on 10 484 pages read
from disk, 6.7 s cold; with other load it passed the 10 s statement timeout). Monthly / annual values repeated on daily
rows are now read from the first day of each period through the (district, date) index (checked on Supabase: every
repeated column is constant within its period and its first day exists for every district-period): Western Cape 624
pages, Ehlanzeni 104, WMA5 2 184; the three requests answer uncached in 1.6 / 0.6 / 1.1 s. `PREWARM_SCOPES` (default
national, province=WC, municipality=DC32, wma=WMA5 — the frontend's test scopes) are pre-loaded for every group
(176 entries, ≈ 38 s at startup). A database-side layout change for long daily series is proposed in
`migrations/proposed/003_daily_tables_layout.sql` with measured gains.

### 13.9 Runoff anomaly from `7.2_derived_runoff_indicators_master`; `testing_table` ignored

Additions only; values change slightly.
- `runoff/summary` KPI `anomaly`, `runoff/timeseries?metric=runoff-anomaly`, `runoff/map?layer=runoff-anomaly`,
  `runoff/breakdown?metric=by-wma` (`lt`) and the runoff entity card use the same-month means stored in 7.2 instead of
  the API's 2000–2025 means (DATA_GAPS §20). For one district the monthly anomaly is 7.2's `Runoff_Anomaly_percent`.
  `source` adds `7.2_derived_runoff_indicators_master`; `baseline` is "2000–2026 same-month mean (stored in 7.2)"
  (map / by-WMA: "same-period mean"); the anomaly KPI `sublabel` reads "vs 2000–2026 same-month mean (stored in 7.2)";
  the anomaly series set has a `note`. Values move by 2.6 pp on average (r = 0.997).
- SSI and flood frequency unchanged (7.2's columns failed the checks and are withheld).
- Categories: new category `derived-runoff-indicators` (§7.2, page `runoff`): `runoff_anomaly_monthly` live
  (diverging bars, %), the other nine columns withheld with reasons. New variables in the registry:
  `runoff_anomaly_monthly`, `runoff_ratio_trend`, `flood_flow_2yr` … `flood_flow_100yr`, `response_time`.
- `testing_table` is no longer listed under `unregistered` in the catalogue (registry `ignored_tables`).

## 14. §8–§16 tables and modules (2026-10-20)

All additions; nothing existing changed shape. Decisions behind them: `docs/DECISIONS.md`.

### 14.1 New modules on the database view

`/api/v1/{module}/categories` and `/category/{tableId}/{groupId}` now exist for `dams`, `surface-water`,
`groundwater`, `groundwater-reserve`, `water-quality`, `green-drop`, `blue-drop`, `rwqo` and `iwrm` (same shapes as
§11–§12). `/health` lists every module; a module whose page only has the database view reports
`status: "partial"` with a reason.

### 14.2 Series resolution, rows after today, impossible values

- Category series of monthly / annual columns are stamped at the start of their period (as before) even when the table
  stores them on the 15th, the 25th or the last day (`10_1`, `12.x`, `10_2`).
- Rows dated after today are never served, counted in `/meta` or used as `newestDataDate` (8_1, 10_1, 10_2 hold rows to
  2026-12-31).
- A variable's `note` says when impossible values are dropped ("Values below 0 are not served: …").
- Yes / no compliance flags are served as **%** (100 = compliant; a scope value is the share of the area compliant);
  `unitOverride` says so.

### 14.3 `dams` (spec §8)

- `summary`: the 8 contract KPIs, every `value` null with `unavailableReason` and an `unavailable` entry: `8_1` storage
  is a placeholder (51 of 52 districts at 100 % FSC every day; DECISIONS D2).
- Every `timeseries`, `breakdown`, `table` and `map` key of the contract → `501 DATA_UNAVAILABLE` with the specific
  reason (maps also: no coordinates in the database). `forecast` → 501.
- New map layers (district choropleths, `geometry: "districts"`, latest complete month, `date` = month start,
  `periodEnd`): `catchment-rainfall` (month total, mm, scale `rainfallMonthly`; 8_1 rainfall ends 31 Aug 2026 because
  it is forward-filled after that) and `soil-moisture` (month mean, m³/m³, scale `soilMoisture`).
- `entities`: one combined reservoir per district from `8_1` (`type: "dam"`, `name` = `dam_name`, `sub` = district ·
  river, `municipality` = MDB code). `entities/{code}` (MDB code): district, province, river, storage / volume /
  alert level (null), catchment rainfall (month total, mm) and soil moisture (month mean) for the latest complete month.
- **`EntityDetail.unavailable`** (new, optional): `[{field: "fields.<label>", reason}]` for card fields returned as null.

### 14.4 `surface-water` (spec §9)

District flows from `9_1` (DECISIONS D10): discharge (m³/s) = stored daily mean flow (m³/day) / 86 400; a scope's
flow is the sum over its districts.

- `summary`: `discharge` (latest complete month's mean flow, sum over the scope, spark = 12 months), `stage`, `high` /
  `low` (**districts** above the 90th / below the 10th percentile of their own monthly flows, full years
  2000–last full year; labels "Districts with High / Low Flow"), `sediment` (mg/L); `stations`, `flood`, `eflow`,
  `wqi` null with reasons.
- `timeseries`: `discharge`, `fdc`, `seasonal`, `water-level`, `sediment` (selector **`kind`**: `conc` = concentration
  mg/L, `load` = daily sediment load t/day, monthly mean), `flow-anomaly`; `entityId` selects a district on discharge,
  water-level, sediment, fdc, seasonal, flow-anomaly and the statistics table. `baseflow` → 501.
- `breakdown=status`: districts per class High Flow / Normal / Low Flow; `eflow`, `risk`, `abstraction` → 501.
- `table`: `top-stations` (districts by mean flow; selector **`order`** `highest` / `lowest`; column "District"),
  `statistics` (discharge, stage, velocity, sediment concentration, sediment load: current 12 months, mean
  2000–last full year, anomaly %, Sen's slope per decade; row `unit` / `decimals`); `gauge-quality`, `alerts` → 501.
- `map`: new district layers `discharge` (m³/s, scale `flow`); `flow-status` (classes, scale `flowStatus`),
  `flow-anomaly` (%, `anomalyPct`) and `ssi` (SSI-3 from the stored discharge, scale `spi`) are **district
  choropleths** instead of points; `annual-runoff`, `runoff-anomaly`, `monthly-runoff`, `runoff-coefficient` are the
  runoff module's layers; `stations`, `catchment-runoff` → 501.
- `entities`: the 52 districts (`type: "district"`); cards: mean flow, flow status, SSI-3 (with a drought-scale
  `status`), stage, velocity, sediment, catchment area, tree cover, built-up share.

### 14.5 `groundwater` (spec §10)

Depth to water from `10_1` NGA telemetry (irregular days, ~every 7 weeks; a month = mean of its samples, unsampled
months are gaps). Comparisons use 12-month means (last 12 months vs the 12 before).

- `summary`: `level` (m bgl, latest sampled month), `anomaly` (m, + = shallower, vs the same-month mean of the full
  years), `recharge` (11.2 direct recharge, mm/yr, latest complete year), `abstraction` (11.3 total abstraction,
  Mm³/yr, summed), `rising` (districts whose 12-month mean depth is shallower than the 12 before; label "Districts with
  Rising Levels"); `boreholes`, `sgi` (valid SGI ends Aug 2024), `risk` null with reasons.
- `entityId` selects a district on `level`, `level-long`, `sgi`, `seasonal`; `table=lowest` selector **`order`** `deepest` / `shallowest`.
- `timeseries`: `level` (water level, previous year, same-month long-term mean; inverse axis), `level-long` (annual
  means, long-term mean, Sen's slope line), `anomaly`, `sgi` (hydrological_drought_master, served to Aug 2024, stale;
  when the page period has no valid SGI its last 12 valid months are shown), `seasonal` (last 5 years by month),
  `recharge-abstraction` (category = year; recharge = direct recharge × district area, abstraction, net; Mm³/yr);
  `quality` → 501.
- `breakdown=distribution` (districts per depth class of the `gwDepth` palette); `status`, `by-aquifer`, `sector` → 501.
- `table`: `lowest` (districts by depth, column "District", 12-month change Rising / Falling), `declining` (12-month
  change and Sen's slope, + = rising); `aquifers`, `nitrate` → 501.
- `map`: `gw-level` (m bgl), `level-change` (m, + = higher), `sgi` (Aug 2024, stale) as district choropleths;
  `status`, `ec` → 501.
- `entities`: 52 districts; card: depth, 12-month change, both hydraulic heads (m amsl, D7), recharge fraction,
  pumping rate and abstraction per borehole, direct recharge, vadose-zone soil moisture.

### 14.6 `groundwater-reserve` (spec §11)

- `summary`: `units` ("Districts with Aquifer Records": no aquifer units in the database), `recharge` (Mm³/yr = 11.2
  direct recharge × area + indirect recharge, DECISIONS D19), `abstraction` (11.3, Mm³/yr), `dynamic` (Mm³/yr) and
  `static` (Mm³) reserves (11_1, summed), `ei` (stored exploitation index in %, with `status` on the `exploitation`
  scale from the ratio), `risk` (districts with EI ≥ 0.5).
- `entityId` selects a district on `recharge-abstraction` and `components`.
- `timeseries`: `level-trend` (11_1 depth to water, long-term mean, Sen's slope line), `recharge-abstraction`
  (category = year, Mm³/yr); `yield-ei` → 501.
- `breakdown`: `ei-classes` (districts per class), `components` (dynamic reserve and current abstraction; the
  ecological reserve and the additional allowable abstraction are null, named "(not in the database)").
- `table`: `aquifers` (districts: recharge, abstraction, EI as a ratio, static reserve, status class), `lowest` (the
  groundwater page's table).
- `map`: `ei` (ratio, scale `exploitation`), `recharge` (direct recharge, mm), `gw-level` (11_1 depth), `sgi` (the
  groundwater page's layer, Aug 2024, stale).
- `entities`: 52 districts; cards with aquifer properties, reserves, recharge, yield, depletion rate and EI status.

### 14.7 `water-quality` (spec §12)

Guidelines, compliance, ratios and the CCME WQI: DECISIONS D20.

- `summary`: `compliance` (% of values within guideline, latest complete month), `exceeding` (districts with any
  parameter outside its guideline), `wqi` (CCME, 12 months; area-weighted for a scope; `status` on the `wqi` scale),
  `poor` (WQI < 50), `risk` (WQI < 25); `stations`, `dams`, `groundwater` null with reasons (no stations or water-body
  types).
- `timeseries`: `param-trend` (selector **`param`**: ec, ph, turbidity, do, bod, cod, nitrate, phosphate, ecoli; this
  year and last by month; `refLines` = the guideline), `station-wqi` (WQI and its long-term average), `group`
  (selector **`group`**: nutrients / metals (combined columns as stored, µg/L) / micro (log axis)); `entityId` selects a
  district on these and on `table=latest`.
- `breakdown`: `param-range` (min / average / max across districts and the guideline; `unit: "mixed"`, `units`),
  `compliance`, `wqi-distribution`, `by-province` (stacked WQI classes), `param-compliance` (% of districts within
  guideline); `by-type`, `risk-outlook` → 501.
- `table`: `latest` (parameter, value, unit, guideline, status), `hotspots` (district × parameter outside guideline,
  worst ratio first; `water` null), `ranking` (selector **`order`** `poorest` / `best`; WQI, class, change vs 12 months
  ago; `river` null); `counts`, `forecast-indicators` → 501.
- `map`: `wqi` (scale `wqi`) and `ph`, `do`, `ec`, `turbidity`, `nitrate`, `phosphate`, `ammonia`, `bod`, `cod`,
  `ecoli` as **district choropleths** of value / guideline (unit "× guideline", scale `ratio`); `lead` (stored only as
  Lead/Cadmium/Chromium) and `forecast-wqi` → 501.
- `entities`: 52 districts; card: WQI with class `status`, latest EC, pH, turbidity, DO, nitrate, phosphate, E. coli.

### 14.8 Drought cascade: streamflow and groundwater stages filled (DECISIONS D22)

- `streamflow` row: `metric: "ssi"`, `scale: "drought"`, `value` "SSI-3 x.xx", `valueNum` = area-weighted SSI-3 of the
  `9_1` district flows (equal to `surface-water/map?layer=ssi` for one district), `cls` / `class` = SPI drought class,
  `severity` = % of area at D1 or worse, `asOf` = the latest complete flow month; a `streamflow` component. Null with
  the calibration reason when the record is too short to fit.
- `groundwater` row while no current SGI exists: `stage` "Groundwater (level anomaly)", `metric: "gwLevelAnomaly"`,
  `scale: "status"`, `value` "±x.xx m vs <Month> mean 2000–<year>", `valueNum` = the Groundwater page's Level Anomaly
  KPI (+ = shallower); `cls` / `bar` / `class` / `severity` null, listed in `unavailable` (`rows.groundwater.cls` …)
  with the SGI reason; a `groundwater` component. With a current SGI the row returns to 12.7.
- Stages without a value never carry `asOf` or a component.

### 14.9 Public-release HTTP behaviour (DECISIONS D24)

- `Cache-Control: public, max-age=<s>` on 200 and 501 responses (summary / map 900, other module endpoints 3600,
  meta / reference / categories 3600); `no-store` on `/health` and other errors.
- New errors: 413 `{"error": "Request body too large", "code": "PAYLOAD_TOO_LARGE"}`, 414 `{"error": "Query string too
  long", "code": "URI_TOO_LONG"}`. Only GET (and CORS preflight) is allowed.
- CORS: `Access-Control-Allow-Origin: *` unless `CORS_ORIGINS` names origins; never credentials.

## 15. Forecasting Studio and page forecasts (2026-10-23 / 29) — new endpoints, additions, forecast keys now 200

NIWIS makes its own statistical forecasts from the database (DECISIONS D29–D39; results on real data in
`docs/FORECAST_VALIDATION.md`). Nothing is written to the database.

### 15.1 Forecasting Studio API (`/api/v1/forecast/*`, new)

| Request | Returns |
| --- | --- |
| `GET /forecast/models` | `{choices: [auto, compare], groups, models: [{id, label, group, groupLabel, description, minHistoryMonths, minHistoryDays, supportsInputs, requiresInputs, resolutions, experimental, nativeInterval}], limits: {monthlyHorizon, dailyHorizon, minHistoryMonths, minHistoryMonthsDeep, minHistoryDays, maxInputs, runsPerHour, timeoutSeconds, retentionHours}, disclaimer}` |
| `GET /forecast/targets?module=` | the live numeric daily / monthly catalogue columns, per table: `{table, column, label, name, unit, monthlyUnit, resolution, resolutions, aggregate (sum \| mean), classified, firstDate, lastDate, historyMonths, enoughHistory, note?}` |
| `POST /forecast/runs` body `{table, column, scope: {level: national \| province \| district \| wma, id?}, model, horizon, inputs: [{table, column}], resolution: monthly \| daily}` | **202** `{runId, status, cached, statusUrl}` + `Location`. Errors: 400 (unknown table / column / model / scope, horizon out of range, withheld or duplicate column), **422 `UNPROCESSABLE`** (`{error, code, issues}`: fewer than 36 months, constant series, more than 20 % missing, an input ending before the target), **429 `RATE_LIMITED`** with `Retry-After` (10 new runs per hour per address; cached results are free), **503 `BUSY`** with `Retry-After` (queue full). The API's only POST; CORS allows it. |
| `GET /forecast/runs/{runId}` | `{runId, status: queued \| running \| done \| failed, progress 0–1, message, queuePosition?, request, createdAt, startedAt?, finishedAt?, expiresAt?, result?, error?: {code, detail}}`, `Cache-Control: no-store`. 404 after `retentionHours` (24 h). |
| `GET /forecast/pages` | status of the precomputed page forecasts (variable, scope, status, model, skill, data date). |

`result` (when `done`): `label` "Statistical forecast", `disclaimer`, `target {table, column, label, unit, resolution,
aggregate, classified, note}`, `scope {level, id, name}`, `inputs[]`, `sources[]`, `dataAsOf`, `model {id, label,
group, experimental, params, why, interval: model \| backtest residuals}`, `history [[date, value]]`, `historyStart`,
`forecastStart`, `asOf` (last observed period = the divider), `backtest [{origin, data: [[date, actual, predicted,
lower, upper]]}]`, `backtestOrigins`, `forecast [[date, median, p10, p90]]`, `metrics {mae, rmse, mase, skill,
smape \| mape, coverage, n}`, `metricNotes`, `comparison [{model, label, group, baseline, experimental, winner, mae,
rmse, mase, skill, smape \| mape, coverage, seconds, error?}]` (every model that ran, baselines always), `warnings[]`
(plain language), `classes [{date, class, probabilities {None, D0 … D4}, pDrought}]` for SPI / SPEI targets,
`runtimeSeconds`.

Scopes: `district` takes a district / metro MDB code (`DC44`, `CPT`), `province` a province code, `wma` `WMA1 … WMA9`.
The same request on the same data returns the same `runId` (`cached: true`); new data give a new run.

### 15.2 `forecast` provenance block (addition to every response type)

Responses made from a forecast carry `forecast: {label: "Statistical forecast", model, modelLabel, why?, skill, mase,
coverage, beatsClimatology, skillNote?, dataAsOf, forecastStart, horizon, generatedAt, disclaimer, studio?}`.
`disclaimer` is always "Statistical forecast from historical data; not an official SAWS / DWS outlook." `skillNote` is
"This model does not beat the long-term average for this variable." when `beatsClimatology` is false. `studio` is the
Forecasting Studio URL that reruns the forecast (absent for derived page variables). Tables and matrices that combine
several forecasts carry the oldest data date and the weakest skill, `model: "auto"`.

### 15.3 Contract forecast keys now served (were 501) — from the precomputed page forecasts (D37)

Auto 24-month forecasts for South Africa and the nine provinces, recomputed when the newest database date changes;
other scopes (district, WMA) answer 501 pointing to the Studio. Forecast `SeriesSet`s: `band` (10–90 %, joined to the
last observation), `obs` (last 36 months), `median` (dashed), `asOf` = the last observed month (the divider),
`horizon` query parameter 3 / 6 / 12 / 24 (default 12), `note` with model, skill and data date.

| Module | Key | Series | Notes |
| --- | --- | --- | --- |
| drought | `forecast?metric=spi` | SPI-3 (`scale=3`, default) or SPI-6 (`scale=6`) | `scale` selector, 400 for other values |
| drought | `forecast?metric=spei` (new key) | SPEI-3 | |
| drought | `forecast?metric=area` | % of the area in D1–D4 (district SPI-6) | 0–100 % |
| drought | `table?metric=forecast-indicators`, `forecast-kpis` | rows `spi-3`, `spi-6`, `spei-3`, `drought-area`: `indicator, unit, current, h3, h6, h12, h24, model, skill` | a row without a forecast is null + `unavailable` |
| drought | `breakdown?metric=class-probability` | categories 1–6 months, series `None, D0 … D4` (%) stacked | from the SPI-3 forecast |
| drought | `matrix?metric=province-risk` | provinces × 3 / 6 / 12 / 24 months, P(D2+) 0–1, `scale: index01` | from each province's SPI-3 forecast |
| rainfall | `forecast?metric=rainfall` | monthly rainfall (mm) | ≥ 0 |
| rainfall | `forecast?metric=spi` | SPI-3 (`scale=3`, default) or SPI-1 (`scale=1`) | was documented as SPI-6 |
| climate | `forecast?metric=temp-anomaly`, `hot-days` (new keys) | monthly temperature anomaly (°C); hot days (Tmax > 35 °C) per month | |
| vegetation | `forecast?metric=ndvi`, `vci` (new key) | NDVI; VCI (%) | `table?metric=forecast-indicators`: rows `ndvi`, `vci` |
| runoff | `forecast?metric=runoff`, `runoff-anomaly` (new key) | runoff depth (mm/month); 7.2 runoff anomaly (%) | `flow` → 501 (modelled per district; the gauged forecast is on surface water) |
| surface-water | `forecast?metric=flow`, `runoff` | river discharge (m³/s, 9_1); runoff depth | `flow` no longer needs `entityId` |
| groundwater | `forecast?metric=level` | depth to groundwater (m bgl) | 501 while the telemetry has too many unsampled months (38 % nationally) |
| ai | `forecast?metric=drought`, `streamflow`, `groundwater` | SPI-3; river discharge; depth to groundwater | `reservoir`, `demand`, `quality`, `scenario` → 501 with reasons |
| ai | `table?metric=models` | the page forecasts' models and backtest scores (`variable, pages, model, mae, rmse, mase, skill, coverage, dataAsOf, status`) | |
| overview | `forecast?metric=ai` | SPI-3 | |

Still 501, with the reason: district outlook maps (`outlook-*`, `forecast-6`, `forecast-1/2/3`, `forecast-ndvi`,
`forecast-anomaly`, `forecast-12`, `forecast-wqi`), settlements / population, recovery, flood probability, reservoir
storage (D2), demand, water-quality forecasts, climate scenarios, `early-warning` (no alert rules), `importance`,
`scenario`.

### 15.4 HTTP

- CORS `allow_methods` is now `GET, POST, OPTIONS` (POST only for `/forecast/runs`).
- `/forecast/models` and `/forecast/targets` are cached like meta (`max-age=3600`); `/forecast/runs/*` and
  `/forecast/pages` are `no-store`; a 501 "being computed" is `no-store` too.
- New error codes: 422 `UNPROCESSABLE`, 429 `RATE_LIMITED` (also on runs), 503 `BUSY`.
