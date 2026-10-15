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

## 8. Checklist

- [ ] `VITE_DATA_SOURCE=api`, `VITE_API_BASE_URL` set; backend `CORS_ORIGINS` includes the frontend origin
- [ ] API client maps `501 DATA_UNAVAILABLE` → "Sample" state
- [ ] KPI / table / map renderers show `null` as "—" with `unavailableReason` / `unavailable[].reason`
- [ ] `public/geo/districts.json` added; choropleths with `geometry: "districts"` use it
- [ ] Municipality filter lists districts from `/reference/municipalities`
- [ ] Optional: zod schemas extended with the provenance fields; "as of" / stale badges
