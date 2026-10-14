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
| `rainfall` | summary (7 of 8 KPIs), timeseries `monthly`, `anomaly`, `spi-spei`, `intensity`, `dry-wet`, `station` (needs `entityId`); breakdown `seasonal`, `events`; tables `provinces`, `extremes`, `statistics`, `sources`; maps `total`, `anomaly`, `monthly`, `map`, `spi1/3/6/12`, `spei6`, `cdd`, `r20`, `municipal-total`; entities (52 districts) | KPI `activeStations` (*null*); `sources.stations` (*null*); forecasts `rainfall`, `spi`; maps `forecast-1/2/3` |
| `drought` | summary (8 of 10 KPIs); timeseries `spi`, `rain-spei`, `rain-anomaly`, `dam-flows`, `gw-sgi` (SGI only), `soil-moisture`, `vegetation`, `drought-area`; breakdown `severity`, `province-area`; tables `cascade`, `affected`, `municipalities`, `sources`; matrix `veg-heatmap`; maps `drought-class`, `municipal-class`, `province-class`, `spi1/3/6/12/24`, `spei3/12`, `rain-anomaly`, `soil-moisture`, `ndvi`, `vci` | KPIs `ssi` (withheld), `settlementsAtRisk` (*null*); `gw-sgi` borehole level (*null* series); timeseries `storage-vs-spi`, `population-at-risk`, `recovery-province`; breakdown `restrictions`, `class-probability`; tables `early-warning`, `forecast-indicators`, `recovery`, `forecast-kpis`; matrix `province-risk`; maps `catchment-class`, `dams`, `boreholes`, `settlements`, `outlook-1/2/3`, `forecast-6`; all forecasts |
| `climate` | summary (8 KPIs); timeseries `temperature-long`, `national-trend`, `extremes`, `heat-days`, `extreme-rain`, `evaporation` (ET₀, PET, AET), `streamflow-linkage`, `drought-frequency`, `indicator-trends`; breakdown `seasonal-change`, `map-seasonality`; tables `statistics`, `provinces`, `signal-chain`; maps `temp-trend`, `temp-anomaly`, `mean-temp`, `rain-trend`, `hot-days`, `cdd`, `et0` | pan evaporation and VPD series (*null*); `seasonal-change` 1981–2000 (*null*); `signal-chain` severity (*null*); timeseries `projections`, `irrigation`; breakdown `risk-index`, `projection-bars`; tables `forecast-summary`, `risk-outlook`; maps `vulnerability`, `proj-*`; `forecast` has no keys (404) |
| `vegetation` | summary (8 KPIs); timeseries `indices` (NDVI), `index-trend`, `anomaly`, `drivers`; breakdown `classification`, `by-province`, `seasonal`, `drivers`; tables `provinces`, `hotspots`; maps `ndvi`, `vci`, `vhi`, `tci`, `ndwi`, `lst`, `sm`, `ndvi-anomaly`, `ndvi-trend` | EVI / SAVI / MSAVI2 series (*null*); maps `evi`, `savi`, `msavi2`, `vpi`, `asi`, `svi`, `ndii`; breakdown `land-cover`; table `forecast-indicators`; forecasts and forecast maps |
| `runoff` | summary (5 of 8 KPIs); timeseries `discharge`, `runoff-anomaly`, `ssi`, `rainfall-runoff`, `water-balance`, `flood-frequency`, `fdc`, `seasonal`, `national-volume`, `drivers`; breakdown `by-wma`; tables `statistics`, `top-stations` (districts), `events`; maps `annual-runoff`, `runoff-anomaly`, `monthly-runoff`, `runoff-coefficient`; entities (52 districts) | KPIs `peakFlow`, `stationsHighFlow`, `stationsLowFlow` (*null*); `events` severity (*null*); breakdown `by-province`; maps `catchment-runoff`, `stations`, `flow-status`, `flow-anomaly`, `ssi`; forecasts |
| all other modules | — | not served by this backend yet (404); keep them on mock data |

Cross-module: `/reference/provinces`, `/reference/wmas`, `/reference/municipalities` (52 districts) are live;
`/reference/catchments` → 501. `/alerts` and `/search` are not implemented yet (404).

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

## 7. Checklist

- [ ] `VITE_DATA_SOURCE=api`, `VITE_API_BASE_URL` set; backend `CORS_ORIGINS` includes the frontend origin
- [ ] API client maps `501 DATA_UNAVAILABLE` → "Sample" state
- [ ] KPI / table / map renderers show `null` as "—" with `unavailableReason` / `unavailable[].reason`
- [ ] `public/geo/districts.json` added; choropleths with `geometry: "districts"` use it
- [ ] Municipality filter lists districts from `/reference/municipalities`
- [ ] Optional: zod schemas extended with the provenance fields; "as of" / stale badges
