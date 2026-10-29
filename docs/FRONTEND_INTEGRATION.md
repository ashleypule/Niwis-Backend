# Connecting the frontend to the NIWIS backend

> For the developer who switches the NIWIS frontend from mock data to this API. Everything here is backwards
> compatible with `reference/API_CONTRACT.md`; the details of each addition are in `docs/CONTRACT_CHANGES.md`.

## 1. Configuration

**Backend** (`.env`, see `README.md`): `SUPABASE_DB_URL`, and `CORS_ORIGINS` = the frontend origin(s). Defaults allow
`http://localhost:5173` and `http://localhost:5174`. Start with `uvicorn app.main:app` or `docker compose up` →
`http://localhost:8000` (interactive docs at `/docs`, health at `/api/v1/health`).

**Frontend** (`.env`):

```
VITE_DATA_SOURCE=api
VITE_API_BASE_URL=http://localhost:8000
```

The client keeps calling `/api/v1/...`; nothing else changes for the live endpoints.

## 2. Three things the frontend must handle

1. **`501` = "Sample".** Panels the database cannot serve return
   `501 {"error", "code": "DATA_UNAVAILABLE", "detail", "missing": [...]}`. The API client currently treats any
   non-2xx as an error; route `code === "DATA_UNAVAILABLE"` to the existing "Sample" state (show the mock data with a
   "Sample" badge, or an empty panel with `detail`).
2. **Nullable fields with a reason.** Inside live responses, a value the DB lacks is `null` and listed in a top-level
   `unavailable: [{field, reason}]`; KPIs also carry `unavailableReason`. Render the KPI as "—" with the reason as a
   tooltip.
3. **District maps.** All data are per **district municipality** (52). Choropleth layers come back as
   `kind: "choropleth"`, `level: "municipality"`, `geometry: "districts"` with `values` keyed by **MDB district code**
   (`DC44`, `CPT`, …). `public/geo/municipalities.json` (213 local municipalities, ids `LM001…`) cannot draw them: copy
   the backend's `reference/geo/districts.json` (52 features, `properties.id` = MDB code, 0.8 MB) into `public/geo/`
   and use it for every `geometry: "districts"` layer. Layers with `level: "province"` keep using `provinces.json`.

## 3. What is live, per module

Status from `/api/v1/health`. "Unavailable" keys return 501 unless marked *null* (field-level).

| Module | Live | Unavailable (501 unless noted) |
| --- | --- | --- |
| `rainfall` | summary (7 of 8 KPIs), timeseries `monthly`, `anomaly` (`window` 1/3/6/12), `spi-spei`, `intensity`, `dry-wet`, `station` (needs `entityId`); breakdown `seasonal`, `events` (`kind` count / max); tables `provinces`, `extremes` (`kind` wettest / driest), `statistics`, `sources`; maps `total`, `anomaly`, `monthly`, `map`, `spi1/3/6/12`, `spei6`, `cdd`, `r20`, `municipal-total`; entities (52 districts) | KPI `activeStations` (*null*); `sources.stations` (*null*); forecasts `rainfall`, `spi`; maps `forecast-1/2/3` |
| `drought` | summary (8 of 10 KPIs); timeseries `spi` (`scale` 1/3/6/12/24), `rain-spei`, `rain-anomaly` (`window`), `dam-flows`, `gw-sgi` (SGI only), `soil-moisture`, `vegetation`, `drought-area` (`scale`); breakdown `severity`, `province-area`; tables `cascade` (contract keys), `affected` (`level=province`), `municipalities`, `sources`; matrix `veg-heatmap`; maps `drought-class`, `municipal-class`, `province-class`, `spi1/3/6/12/24`, `spei3/12`, `rain-anomaly`, `soil-moisture`, `ndvi`, `vci` | KPIs `ssi` (withheld), `settlementsAtRisk` (*null*); `gw-sgi` borehole level (*null* series); `affected?level=catchment`; timeseries `storage-vs-spi`, `population-at-risk`, `recovery-province`; breakdown `restrictions`, `class-probability`; tables `early-warning`, `forecast-indicators`, `recovery`, `forecast-kpis`; matrix `province-risk`; maps `catchment-class`, `dams`, `boreholes`, `settlements`, `outlook-1/2/3`, `forecast-6`; all forecasts |
| `climate` | summary (8 KPIs); timeseries `temperature-long`, `national-trend` (`var` temperature / rainfall / et / streamflow), `extremes`, `heat-days` (`kind` hot / cold), `extreme-rain` (`kind` r20 / rx1), `evaporation` (ET₀, PET, AET), `streamflow-linkage`, `drought-frequency`, `indicator-trends` (observed, no `scenario`); breakdown `seasonal-change` (`var` temperature / rainfall), `map-seasonality`; tables `statistics`, `provinces`, `signal-chain` (contract keys); maps `temp-trend`, `temp-anomaly`, `mean-temp`, `rain-trend`, `hot-days`, `cdd`, `et0` | pan evaporation and VPD series (*null*); `seasonal-change` 1981–2000 (*null*); `signal-chain` severity / `cls` / `bar` (*null*); `indicator-trends` drought series (*null*: zero drought months in the 2020 base year); `indicator-trends?scenario=…` (no projections); timeseries `projections`, `irrigation`; breakdown `risk-index`, `projection-bars`; tables `forecast-summary`, `risk-outlook`; maps `vulnerability`, `proj-*`; `forecast` has no keys (404) |
| `vegetation` | summary (8 KPIs); timeseries `indices` (`set`: NDVI, VCI, VHI, TCI, NDWI, LST, SM), `index-trend` (`index`, same live indices), `anomaly`, `drivers` (`var` rain / temp / pet / sm); breakdown `classification`, `by-province`, `seasonal`, `drivers`; tables `provinces`, `hotspots`; maps `ndvi`, `vci`, `vhi`, `tci`, `ndwi`, `lst`, `sm`, `ndvi-anomaly`, `ndvi-trend` | EVI / SAVI / MSAVI2 / VPI / ASI / SVI / NDII in `indices` (*null* series); `index-trend` for those indices; maps `evi`, `savi`, `msavi2`, `vpi`, `asi`, `svi`, `ndii`; breakdown `land-cover`; table `forecast-indicators`; forecasts and forecast maps |
| `runoff` | summary (5 of 8 KPIs); timeseries `discharge`, `runoff-anomaly`, `ssi`, `rainfall-runoff`, `water-balance`, `flood-frequency`, `fdc`, `seasonal`, `national-volume`, `drivers` (`var` rain / aet / sm); breakdown `by-wma`; tables `statistics`, `top-stations` (districts, `order` highest / lowest), `events`; maps `annual-runoff`, `runoff-anomaly`, `monthly-runoff`, `runoff-coefficient`; entities (52 districts) | KPIs `peakFlow`, `stationsHighFlow`, `stationsLowFlow` (*null*); `events` severity (*null*); breakdown `by-province`; maps `catchment-runoff`, `stations`, `flow-status`, `flow-anomaly`, `ssi`; forecasts |
| `dams` | entities (52 district reservoirs from `8_1`: name, river) + cards (catchment rainfall month total, soil moisture month mean); maps `catchment-rainfall`, `soil-moisture` (additions); categories | every summary KPI (*null*: `8_1` storage is a placeholder, DECISIONS D2); every contract timeseries / breakdown / table / map key; forecasts |
| `surface-water` | summary (discharge, stage, high / low-flow districts, sediment); timeseries `discharge`, `fdc`, `seasonal`, `water-level`, `sediment` (`kind`), `flow-anomaly`; breakdown `status`; tables `top-stations` (`order`), `statistics`; maps `discharge`, `flow-status`, `flow-anomaly`, `ssi` (district choropleths), runoff layers; entities (52 districts); categories | KPIs `stations`, `flood`, `eflow`, `wqi` (*null*); `baseflow`; breakdowns `eflow`, `risk`, `abstraction`; tables `gauge-quality`, `alerts`; maps `stations`, `catchment-runoff`; forecasts |
| `groundwater` | summary (level, anomaly, recharge, abstraction, rising districts); timeseries `level`, `level-long`, `anomaly`, `sgi` (to Aug 2024, stale), `seasonal`, `recharge-abstraction`; breakdown `distribution`; tables `lowest`, `declining`; maps `gw-level`, `level-change`, `sgi`; entities (52 districts); categories | KPIs `boreholes`, `sgi`, `risk` (*null*); `quality`; breakdowns `status`, `by-aquifer`, `sector`; tables `aquifers`, `nitrate`; maps `status`, `ec`; forecast |
| `groundwater-reserve` | summary (districts, recharge, abstraction, dynamic / static reserve, EI with class, districts at risk); timeseries `level-trend`, `recharge-abstraction`; breakdowns `ei-classes`, `components`; tables `aquifers`, `lowest`; maps `ei`, `recharge`, `gw-level`, `sgi`; entities (52 districts); categories | timeseries `yield-ei`; ecological reserve and allowable abstraction (*null* categories) |
| `water-quality` | summary (compliance, districts exceeding, WQI, poor / very poor districts); timeseries `param-trend` (`param`), `station-wqi`, `group` (`group`); breakdowns `param-range`, `compliance`, `wqi-distribution`, `by-province`, `param-compliance`; tables `latest`, `hotspots`, `ranking` (`order`); maps `wqi` + 10 × guideline layers; entities (52 districts); categories | KPIs `stations`, `dams`, `groundwater` (*null*); breakdowns `by-type`, `risk-outlook`; tables `counts`, `forecast-indicators`; maps `lead`, `forecast-wqi`; forecasts |
| `green-drop`, `blue-drop`, `rwqo`, `iwrm` | categories only (`/health`: `partial`) | every contract endpoint (404) |
| all other modules | — | not served by this backend yet (404); keep them on mock data |

Cross-module: `/reference/provinces`, `/reference/wmas`, `/reference/municipalities` (52 districts) are live;
`/reference/catchments` → 501. `/alerts` and `/search` are not implemented yet (404).

Selectors are listed in `CONTRACT_CHANGES.md` §10; every one is honoured (until 2026-10-15 all were ignored), and
an unknown value is a `400` with `issues[0].allowed`.

## 4. Filters

| Param | Behaviour |
| --- | --- |
| `province` | area-weighted from the province's districts (`aggregation: "district-area-weighted"`) |
| `municipality` | district / metro **MDB code**; a local code like `EC101` → 400 with a hint |
| `wma` | district-overlap weighting, flagged `aggregation: "district-overlap"` (approximate) |
| `catchment` | 501 `DATA_UNAVAILABLE` |
| `period`, `from`, `to` | as in the contract; annual charts use the whole record unless `from`/`to` narrow it |
| `entityId` | district MDB code (`rainfall` `station` series, entity cards) |

## 5. Provenance fields (optional additions)

Every response may carry `asOf`, `source` (tables), `isStale`, `components` (per-source dates for combined panels),
`baseline`, `baselineNote`, `aggregation`, `unavailable`; KPIs and entity fields carry their own `asOf` / `isStale`.

- zod's default object parsing **strips unknown keys**, so the current schemas accept these responses unchanged; to
  *show* the fields, add them as `.optional()` to the schemas in `src/types/api.ts` (list in `CONTRACT_CHANGES.md`).
- **Mixed dates are normal:** rainfall and SPI run to 2026-09; satellite (VCI, NDVI, soil moisture) and hydrology
  (dam storage, SGI) end 2025-12 and arrive with `isStale: true`. Show "as of Dec 2025" badges from `asOf`.
- **Baseline:** anomaly titles in the UI say "1981–2010"; the data start in 2000, so use `baseline` /
  `baselineNote` (e.g. "2000–2026 monthly mean — shorter than the WMO 30-year standard").

## 6. Value conventions worth knowing

- `droughtClass` KPI: `value` = class rank 0–5, `text` = `"D1"` / `"No drought"`, `status.scale = "drought"`.
- SPI / SPEI / monthly anomalies are for the **latest complete month** (the current month is partial in the DB).
- Runoff flows and volumes are derived from modelled depth × real district area (not gauged).
- `entities` `type` is `"district"`; entity cards use `image: "municipality"` (rainfall) or `"river"` (runoff).

## 7. Changes on 2026-10-15 (`../Niwis Frontend/docs/backend/BACKEND_ISSUES.md`; details in `docs/ISSUE_FIXES.md`)

- **Vegetation tabs (#1):** `indices?set=vci,vhi,tci` / `set=ndwi,ndii` now return the requested indices (NDII as a
  null series with an `unavailable` entry), so the "Condition" and "Water content" tabs' set check passes and they
  render on their own. Every other selector is honoured too (§3 above); tabs that relied on them (SPI scale, anomaly
  window, wettest / driest, hot / cold days, R20 / Rx1day, national-trend variable, drivers variable, highest /
  lowest) now change the data.
- **Cascade (#2, #3):** rows carry `{stage, metric, value, cls, scale, bar}`; `CascadeFlow`'s adapter still works and
  can be reduced to the contract shape. Drought `value` is now text (`SPI-6 0.88`, `VHI 87 %`, `63.6 % FSC`), the
  number is `valueNum`; `bar` is 0–100 like the mock.
- **Rate limit (#4):** 600 / min per IP by default; `/health` exempt. The client's `Retry-After` back-off can stay.
- **Nulls (#5, #6):** every all-null series / KPI / column / map has an `unavailable` entry (KPIs also
  `unavailableReason`); a field listed in `unavailable` is always null, so "hide what is listed" and "hide what is
  all-null" now agree. `seasonal-change` p0 is null.
- **Staleness (#7):** measured from the end of the period; annual values for the last complete year (2025) are no
  longer stale. New optional `periodEnd` (monthly / annual values) for "Data to Dec 2025"-style labels.
- **"No drought" for missing data:** removed; a missing SPI now has no class / pill (only visible on scopes with a
  missing district).

## 8. Database category charts (2026-10-15)

DWS asked for every dataset to be shown **as it exists in the database**: category = table, variable = column, label
and unit = from the column name. Nothing from the mockup's names or groupings is used here; the full list of tables,
columns, labels, units, chart groups and statuses is `docs/CATEGORY_CATALOGUE.md` (generated from the database
schema by `python scripts/build_catalogue.py`).

### Endpoints (every module: `rainfall`, `drought`, `climate`, `vegetation`, `runoff`)

| Request | Returns |
| --- | --- |
| `GET /api/v1/{module}/categories` | `{module, asOf, source, aggregation, tables: [...]}`. One entry per table shown on that page: `id`, `table` (exact DB name), `label` (from the table name), `spec` / `specTag` (`"§3.1"`), `resolution`, `asOf` / `isStale`, `counts` (`live`, `withheld`, `duplicate`), `groups` and `variables`. |
| `GET /api/v1/{module}/category/{tableId}/{groupId}?vars=…` | A `SeriesSet` (same shape as `/timeseries`), one series per column. `vars` = comma-separated **exact column names**, `all`, or absent (= the group's `defaultVisible`). The usual filters apply (`province`, `wma`, `municipality`, `period`, `from`, `to`); `entityId` selects one district. |

Each **group** is one chart: `{id, label, unit, resolution, columns, defaultVisible, path}`; `path` is the series URL.
Each **variable** (column): `column`, `label` (with unit, e.g. "Daily Rainfall (mm/day)"), `name` (without),
`unit`, `kind` (`numeric` / `categorical`), `resolution`, `status`, `reason` (withheld), `duplicateOf` (duplicate),
`group`, `defaultVisible`, `asOf` / `isStale` / `periodEnd` (live), `source` (`table.column`), `note` (method /
caveat, show as a tooltip), `unitOverride` (why the unit is not the name's suffix).

Series (`key` = column name, `name` = label) add `unit`, `asOf`, `isStale`, `periodEnd`, `source` (`["table.column"]`),
`resolution` and `status`.

### Table → module pages (`category_modules` in `app/registry/datasets.yaml`)

| Page | Tables |
| --- | --- |
| Drought | Meteorological Drought §3.1, Satellite Remote Sensing §3.2, Hydrological Drought §3.3, Rainfall Derived Drought Indices §6.2 |
| Rainfall | Wet Dry Spells §4.2, Mean Annual Precipitation §4.3, Core Rainfall Variables §6.1, Rainfall Derived Drought Indices §6.2 |
| Climate | Temperature Change §4.1, Wet Dry Spells §4.2, Mean Annual Precipitation §4.3, Potential Evaporation Daily §4.4, Irrigation Demand Daily §4.5, Streamflow and Climate Linkage Annual §4.6 |
| Vegetation | Satellite Remote Sensing §3.2, Primary Satellite Vegetation Variables SANSA §5.1, Meteorological Drivers SAWS §5.2 |
| Runoff | Hydrological Drought §3.3, Runoff Variables §7.1 |

Render one card per table (`label` + `specTag`), one chart per group, and a toggle per column of the group (all
columns of the group can be switched on; start with `defaultVisible`). Order tables and groups as returned.

### Labels and units

From the column name only: the unit is the name's suffix (`_mm_day` → mm/day, `_C` → °C, `_pct` / `_percent` → %,
`_m3_s` → m³/s, `_million_m3` → Mm³, `_km2` → km², `_wm2` → W/m², `_days` → days, …; `_dimensionless` /
`_standardised` → no unit); the label is the rest with underscores as spaces, words capitalised and acronyms kept
(SPI, SPEI, NDVI, ET₀, PRCPTOT, …). Use `label` as is; do not rename. Seven unit overrides exist where the name has no
unit or the wrong one (`unitOverride` says why), e.g. satellite `vci` → %, GDD → °C·days.

### The axis rule

A group never mixes units, so a category chart always has **exactly one axis**: `axes[0].unit` = the group's `unit`.
Unitless standardised indices (SPI, SPEI, SSI, SGI, RAI, SVI) share an `index` axis; spectral indices (NDVI, NDWI,
NDII, EVI, SAVI, MSAVI2) are a separate group with their own `index` axis; NDVI and VCI / VHI (%) are never in the
same chart. Do not merge groups into one chart.

### Status values

| Status | Meaning | What the UI does |
| --- | --- | --- |
| `live` | Served from the column | Plot it. |
| `withheld` | Not served: placeholder, failed plausibility check, or a table under review — `reason` says which | List the column with its reason; its series is all-null with an `unavailable` entry (`series.<column>`). |
| `duplicate` | The same values as another column (`duplicateOf`) **that this page also shows** | List it ("same as …"); it is not in any group on this page and cannot be requested here. On a page that does not show its twin the column is `live`, in a group, with `note` "Same values as …" and `duplicateOf` kept. |

Today: 151 columns, 62 live, 60 withheld, 29 duplicate; 105 chart groups over the five pages (`docs/CATEGORY_CATALOGUE.md`). Groups are **per page** (a page may show duplicates another page hides): always use the `groups` and `path`s of the page's own `/categories`. Monthly and annual values the database repeats on daily rows are read once per period; incomplete months / years are never served; daily groups longer than 2 000 points come back as monthly means (`note` says so). Volumes, flows and areas are summed over the scope's districts (all needed); everything else is an area-weighted mean (≥ 95 % of the area). A column whose early rows are a fill is served from its first real period (satellite soil moisture from Mar 2015; the variable's `note` says so).

### Recommended chart type

Each variable has `chart`: `bar` (totals and counts: rainfall amounts, seasonal and monthly totals, R10mm / CDD days, volumes, degree-day sums), `diverging-bar` (anomalies: bars around 0) or `line` (states and indices: temperatures, humidity, soil moisture, SPI / SPEI / RAI, NDVI …). Each group has `chart` — the members' common type, `band` when it holds a min / max pair (then `band: [min column, max column]`, e.g. Tmin / Tmax: draw the band between them, other columns as lines), or `mixed` (draw each series by its own `chart`). Category series carry `chart` and `kind: "bar"` for bar types; the SeriesSet carries the group's `chart` / `band`.

### Speed

Every group of every page is pre-loaded at startup for the default request (national, `period=12M`, `vars=all`) and kept warm, so the first view of a page answers from cache (a few ms per chart). Other scopes are computed on demand in one table scan per group (≈ 2 s cold for a daily group, then cached for an hour).

### Contract changes for the frontend step

All additions unless marked; details in `docs/CONTRACT_CHANGES.md` §11–§12.

1. **New endpoints** `/{module}/categories` and `/{module}/category/{tableId}/{groupId}` (above); new response type
   `CategoryList`; `Series` gains optional `unit`, `asOf`, `isStale`, `periodEnd`, `source`, `resolution`, `status`.
2. **`entityId`** now selects the district on runoff `discharge`, `fdc`, `flood-frequency`, `seasonal`, runoff
   `statistics` and every category chart (as rainfall `station` did); unknown code → 404.
3. **`decimals`** on every numeric table column; mixed-unit statistics tables (runoff, rainfall) also give each row
   `unit` / `decimals` (runoff `trendDecimals`). Prefer the row's value when present.
4. **`climate/timeseries?metric=drought-frequency`** (changed): series `d2`, `d3`, `d4` (new, stacked % of area, axis
   0), `trend` (axis 0), `months` (**redefined**: district-months in D2+, axis 1); axes `%` and `district-months`.
5. **Drought Class KPI** (changed meaning): worst class covering ≥ 20 % of the area; `sublabel` gives the % in D1–D4.
   SPI-3 / SPI-6 / SPEI-12 KPIs are labelled "(area-weighted mean)" on aggregated scopes. Province-class map and the
   affected table's `class` use the same area rule.
6. **`vegetation/breakdown?metric=drivers`** (changed): `unit` = `"mixed"`, new `units` per category, unit in each
   category name; temperature / LST in °C, GDD in °C·days, rainfall / ET₀ in %. `climate indicator-trends`: `temp`
   in °C on a second axis (`axis: 1`).
7. **Notes / reasons**: developer wording removed (text only).
8. **Drought cascade** (changed): 7 rows in spec order with `stageId` / `order`; new rows `soil-moisture`,
   `streamflow`, `groundwater` (scale `status`); reservoir (`hydrological`) null with the plausibility reason.
9. **SSI**: drought `ssi3` now has values (from modelled runoff), labelled "SSI-3 (modelled runoff)"; runoff SSI
   labels likewise.
10. **Flood frequency** at 2, 5, 10, 20, 50, 100 years (1.25 and 25 gone).
11. **FDC** `lt` = 2000–2025 (was 2011–2020).
12. **`rainfall/breakdown?metric=seasonal`** (changed keys): `jfm`, `amj`, `jas`, `ond` (was `djf` … `son`).
13. **`vegetation/map?layer=ndvi-trend`**: new `legend` (5 data-derived classes `{from, to, color, label}`); use it
    instead of the `ndviTrend` palette when present.
14. **Sources table**: TAMSAT cadence `monthly`.
15. **Withheld after the plausibility checks** (values → null + reason): dam storage, % FSC, inflow, outflow
    (Dam Storage KPI null, dam-flows → 501), solar radiation, GRACE, the `4_3` record statistics. Show the reason.
16. **Duplicates per page** (changed, 2026-10-16): see Status values; groups differ per page.
17. **`chart`** on variables, groups, series and series sets; `kind: "bar"` for bar types (BACKEND_ISSUES #8).
18. **No null `asOf` / `periodEnd`** anywhere (they are omitted; #10).
19. **`source: ["table.column"]`** on summary series that plot one stored column (#11).
20. **Vegetation classes** Excellent / Good / Fair / Poor / Very Poor (#12): `normalizeVegClasses()` can go.
21. **SGI served May 2002 – Aug 2024 only** (values after repeat Aug 2024): `gw-sgi` and the category view serve it
    marked `isStale`, `periodEnd` 2024-08-31, with a note; the SGI KPI and cascade groundwater stage stay null
    ("Latest valid SGI is Aug 2024 (more than 90 days old)"). satellite soil moisture served from Mar 2015 (labelled 2015–2025).
22. **Runoff anomaly from `7.2`** (2026-10-20): the runoff anomaly (KPI, chart, map, by-WMA long-term, entity card)
    uses the same-month means stored in `7.2_derived_runoff_indicators_master`; the baseline label reads "2000–2026
    same-month mean (stored in 7.2)" — show `baseline` from the response rather than a fixed "2000–2025". New category
    `derived-runoff-indicators` on the runoff page (one live chart; SSI, flood flows, trend and response time
    withheld with reasons). `testing_table` no longer appears under unregistered tables.

## 9. Checklist

- [ ] `VITE_DATA_SOURCE=api`, `VITE_API_BASE_URL` set; backend `CORS_ORIGINS` includes the frontend origin
- [ ] API client maps `501 DATA_UNAVAILABLE` → "Sample" state
- [ ] KPI / table / map renderers show `null` as "—" with `unavailableReason` / `unavailable[].reason`
- [ ] `public/geo/districts.json` added; choropleths with `geometry: "districts"` use it
- [ ] Municipality filter lists districts from `/reference/municipalities`
- [ ] Optional: zod schemas extended with the provenance fields; "as of" / stale badges
- [ ] Database category charts: one card per table, one chart per group, column toggles, withheld / duplicate
      columns listed with their reason (section 8)

## 10. Forecasting Studio and forecast tabs (2026-10-29)

NIWIS makes its own statistical forecasts (DECISIONS D29–D39; what works on the real data:
`docs/FORECAST_VALIDATION.md`). Details of every field: `docs/CONTRACT_CHANGES.md` §15.

### The Studio (`/ai`, module id `ai`)

1. `GET /api/v1/forecast/targets` lists what can be forecast (live numeric daily / monthly columns, with label, unit,
   `aggregate` sum / mean, resolutions, history); `GET /api/v1/forecast/models` lists the models (grouped, with
   descriptions, minimum history, `experimental`) and the limits.
2. `POST /api/v1/forecast/runs` with `{table, column, scope, model, horizon, inputs, resolution}` → `202 {runId}`.
   Answer **400** / **422** / **429** / **503** at once with the server's own message (`error`); show it as is — it says
   what to change (shorter horizon, another column, wait).
3. Poll `GET /api/v1/forecast/runs/{runId}` every second: `status`, `progress`, `message`, `queuePosition`; then
   `result` (see §15.1). A finished run never changes. 404 after 24 h: re-run.
4. The frontend encodes the request in the URL (`?table=…&column=…&level=…&area=…&model=…&horizon=…&inputs=…&run=1`);
   a link with `run=1` re-runs on open, and the server answers from its cache when nothing changed (`cached: true`).
5. The chart is the dashboard's forecast chart: `band` (10–90 %), `obs`, one dashed series per backtest origin, the
   median; `asOf` draws the Historical | Forecast divider. Metrics, the sortable comparison table (winner highlighted,
   baselines always present), SPI class probabilities, warnings and a CSV download come from `result`.
6. The Studio needs the backend (module `ai` in `VITE_LIVE_MODULES`); with sample data it says so.

### Forecast tabs

The forecast endpoints in §15.3 answer 200 for South Africa and the provinces; every response carries `forecast`
(model, skill, data date, disclaimer, Studio link). Panels show, under the chart / table, "Statistical forecast ·
<model> · skill vs climatology <x> · data to <Mon YYYY>" and, when `beatsClimatology` is false, "This model does not
beat the long-term average for this variable." The info tooltip always starts with the disclaimer. Other scopes (a
district, a WMA) and products without a forecast variable stay 501 → "Not yet available" with the reason.

### Fixtures and tests

- `npm run fixtures:record` also records the Studio flow: the POST (keyed by its body, `forecast/runs?body={…}`)
  and the finished run, for the Studio's default choices and one shared link (`scripts/record-fixtures.ts`,
  `STUDIO_RUNS`). In fixtures mode MSW answers a POST from that key (`src/mocks/fixtures.ts`).
- Record after the backend's page-forecast round has finished (`GET /api/v1/forecast/pages`: every item `done`), else
  the forecast tabs record "being computed" 501s.
- Playwright hybrid: `tests/e2e/hybrid/studio.spec.ts` (default run, shared link, forecast-tab note).
