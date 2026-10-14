# Data mapping — contract ↔ database

> Phase 0 deliverable. Maps every dataset key in `reference/API_CONTRACT.md` for the five modules with data
> (`rainfall`, `drought`, `climate`, `vegetation`, `runoff`) to its source table, columns and transformation.
> Evidence: `docs/DB_INVENTORY.md` (profile of every table) and `scripts/data_checks.sql` (targeted checks; run with
> `python scripts/run_checks.py scripts/data_checks.sql`). Problems the data team can fix are in `docs/DATA_GAPS.md`.
>
> **Status:** decisions D1–D5 resolved on 2026-10-14 (see [§0.4](#04-decisions-resolved-2026-10-14)). Rows marked
> ⚑D2 use the area weights from `reference/geo/districts.json` (supplied and validated 2026-10-14). Canonical sources per variable:
> `app/registry/datasets.yaml`.

## 0. What shapes every mapping

### 0.1 The spatial unit is the district municipality

All 14 tables are keyed by **district municipality name** (`district_name` / `District_Name`): 52 units = 44 district
municipalities + 8 metros. There are **no stations, no coordinates, no grid cells, no catchment or WMA codes** in any
table. Consequences:

| Contract expectation | What the API can do with district data |
| --- | --- |
| `entities` = stations (rainfall: 213, runoff: 1842) | Entities = the 52 districts (`type: "district"`), id = MDB code (e.g. `DC44`, `CPT`). ⚑ D1 |
| `map` layers of `kind: grid` | `kind: choropleth`, `level: municipality`, `values` keyed by district MDB code. ⚑ D1 |
| `map` layers of `kind: points` (stations) | No coordinates → **UNAVAILABLE** as points. A choropleth equivalent is offered where the variable exists. ⚑ D1 |
| `province` filter | District → province lookup (each district lies in exactly one province). Fully supported. |
| `municipality` filter | Supported for **district / metro MDB codes** (`DC44`, `CPT`, …). Local-municipality codes (`EC101`) → `400` with a message pointing to the parent district (values are not available below district level). |
| `wma` filter | District values weighted by each district's area of overlap with the WMA (`reference/geo/wmas.json` × district polygons), flagged `"aggregation": "district-overlap"`. ⚑ D2 — until district polygons are supplied → `501 DATA_UNAVAILABLE`. |
| `catchment` filter | `501 DATA_UNAVAILABLE`: districts are far coarser than quaternary catchments (D1). |

District names are not consistent across tables (see Appendix A); the API normalises them through an alias table.

### 0.2 Aggregation methods (district → province / national)

| Variable type | Method | Weight / note |
| --- | --- | --- |
| Depths and intensive values (rainfall mm, temperature °C, ET mm, NDVI, VCI %, runoff mm, soil moisture) | **Area-weighted mean** of district values: Σ wᵢ·xᵢ / Σ wᵢ | wᵢ = district area (km²) from official district boundaries (`reference/geo/districts.json`). ⚑ D2 — until supplied, aggregates are **unavailable** rather than plain means. |
| Anomaly % | Recomputed from the aggregated quantities: (Σw·x − Σw·normal) / Σw·normal × 100 — never an average of percentages | |
| Standardised indices (SPI, SPEI, SSI, SGI, SVI) | Area-weighted mean of district index values; documented as an approximation (a regional SPI proper needs a gamma fit on the aggregated series) | |
| Drought class of an area | Class of the aggregated SPI-6 (thresholds from `core/thresholds.json`) | |
| Area in drought / class shares | Σ area of districts in class / Σ area × 100 | |
| Volumes (dam storage, inflow, derived runoff volume) | **Sum** | storage % = Σ volume / Σ FSC (FSC per district implied by volume / %FSC) |
| WMA scope | Σ wᵢ·xᵢ / Σ wᵢ with wᵢ = area of district *i* inside the WMA | approximate — `aggregation: "district-overlap"` |
| Counts (days ≥ 20 mm, hot days) | Area-weighted mean for "per location" statistics (labelled "district mean") | |
| Extremes (max daily rainfall, peak flow) | Max over districts, with the district named in `sublabel` | |

### 0.3 Time handling

- **Latest data:** daily tables end **2026-09-06**; monthly tables end **2026-09-01**; `satellite_remote_sensing` and
  `hydrological_drought_master` end **2025-12-01** (9 months stale). `asOf` always reports the newest date actually used.
- **Daily tables repeat monthly/annual values on every day** (SPI/SPEI, monthly rainfall, anomalies, heatwave counts,
  seasonal totals …). The API reads one row per district-month for those columns (`DISTINCT ON` / `GROUP BY … max()`),
  never sums them.
- **31 December is missing every year** in `meteorological_drought_master`, `4_1`, `4_4`, `4_5`, `5.1` and in the
  monthly `5.2` December totals. `6.1` and `runoff_variables_master` are complete. Daily rainfall therefore comes from
  `6.1`; December totals from `6.1`.
- **Annual tables include 2026 as a partial year** (Jan–Sep). The API excludes partial years from annual series and
  trends, or labels them "2026 (to Sep)".
- **Baseline.** The contract and UI titles say *1981–2010*. The data start in 2000, and the stored anomalies
  (`rainfall_anomaly_*`, `Monthly_Anomaly_*`, `temp_anomaly_c`, `Temperature_Anomaly_C`) are relative to the
  **2000–2026 per-calendar-month mean** (verified to 3 decimals). ⚑ D3
- **Downsampling:** daily series longer than 2 000 points are aggregated to monthly (sum for rainfall/volume, mean for
  rates, max for extremes) — never thinned by skipping points.

### 0.4 Decisions resolved (2026-10-14)

| # | Decision | Applied as |
| --- | --- | --- |
| D1 | Districts as entities and map units | Entities `type: "district"`, id = MDB code; district choropleths keyed by MDB code. Province / national aggregates area-weighted; **WMA** filter = district values weighted by area of overlap, flagged `"aggregation": "district-overlap"`; **catchment** filter → `DATA_UNAVAILABLE`. Recorded in `CONTRACT_CHANGES.md`. |
| D2 | Real district areas | Weights from official district polygons: `reference/geo/districts.json` (geoBoundaries ADM2, supplied 2026-10-14, validated — `DATA_GAPS.md` §12). The district–WMA overlap covers 99.8–100 % of every WMA. Runoff volume = depth (mm) × real district area (km²) / 1000 → Mm³, labelled derived; the table's 6 500 km² volumes are never used. |
| D3 | Baseline | Stored anomalies labelled `"baseline": "2000–2026 monthly mean"` with `baselineNote` "shorter than the WMO 30-year standard"; anomalies the API computes use 2000–2025 full years and say so. |
| D4 | One vegetation source | `satellite_remote_sensing` passed the plausibility check and serves **both** the drought and vegetation pages; `5.1` failed and is withheld entirely (evidence: `DATA_GAPS.md` §5). |
| D5 | Withhold placeholders / implausible values | Withheld, never rescaled: constant soil moisture and every vegetation index in `5.1`, VPI, hydrology discharge/runoff/baseflow/SSI/FDC/borehole level, all of `4_4`, `4_5`, `4_6`, runoff peak flow, monthly volume, catchment area, curve number, land cover, impervious fraction, wind speed. |
| — | Mixed dates | Every value carries its own `asOf`; combined panels list per-component dates in `components`; `isStale` when a value is > 90 days (configurable) behind the newest DB date. |
| — | Duplicates | One canonical source per variable in `datasets.yaml`; duplicates listed in `DATA_GAPS.md` §13. |

Legend for the tables below: **LIVE** = column served directly · **DERIVED** = computed from DB rows ·
**PARTIAL** = some series/columns available · **UNAVAILABLE — not in DB** · ⚑ = depends on a decision above.
"District" below means "district or metro".

---

## 1. Rainfall — `rainfall` (DB Module 4; spec §6)

Primary tables: `6.1_core_rainfall_variables_master` (daily rows, monthly/seasonal/annual columns repeated),
`6.2_rainfall_derived_drought_indices_master` (monthly SPI/SPEI). Supporting: `4_2_wet_dry_spells_master`,
`4_3_mean_annual_precipitation_master`, `meteorological_drought_master` (SPEI-6, identical rainfall/SPI).

**Implemented (Phase 2, 2026-10-14)** — `app/modules/rainfall/`. Implementation details beyond the tables below:

- **Window totals** are sums of daily rainfall over the exact window (default 12 M ending at the newest daily record,
  2026-09-06), computed in SQL per district; a district needs ≥ 95 % of days. **Normals** = the DB climatology
  (monthly total − stored anomaly, constant per district-month) pro-rated by the days of each month in the window.
- **Partial current month.** The DB's September 2026 rows cover 6 days, but its stored anomaly / SPI compare them with
  a full month. The API therefore uses the **latest complete month** (2026-08) for SPI, SPEI and monthly-anomaly
  series and says so in `sublabel` / `note`; the `monthly` chart and `monthly` map include the partial month, labelled
  "to 6 Sep".
- **CDD (summary, map)** = days since the last day with ≥ 1 mm, up to the newest record (look-back 730 days).
- **Seasonal breakdown** categories = the last 7 full years; **events** = district-days > 50 mm per year, last 10
  years (current year to date).
- **Provinces table** `status` = drought class (scale `drought`) of the province's area-weighted SPI-12.
- **Statistics table** trend = Sen's slope × 10 (per decade) over full years 2000–2025 (`pymannkendall`).
- **Aggregation** below 95 % area coverage returns `null` (never a mean of the districts that happen to have data).
- Verified against SQL: `docs/VERIFICATION.md`.

### Summary (`KpiSet`)

| KPI (`metric`) | Status | Source → transformation |
| --- | --- | --- |
| Total Rainfall (`rainfall`, mm) | DERIVED | `6.1.Monthly_Total_Precipitation_mm`, one per district-month; Σ over period (default 12 M) per district → area-weighted. `spark` = last 12 monthly totals. `delta` vs same period last year. |
| Rainfall Anomaly (`rainfallAnomaly`, %) | DERIVED | normal = `Monthly_Total_Precipitation_mm − Monthly_Anomaly_mm` (DB climatology); (ΣP − Σnormal)/Σnormal × 100. ⚑ D3 |
| Heavy Rain Days R20mm (`heavyRainDays`, days) | DERIVED | count of days with `6.1.Daily_Rainfall_Total_mm ≥ 20` in period, per district → area-weighted ("district mean"). Matches `4_2.R20mm_Days` for full years (verified). |
| Max Daily Rainfall (`maxDailyRain`, mm) | DERIVED | max `Daily_Rainfall_Total_mm` in period over districts in scope; district in `sublabel`. |
| Consecutive Dry Days (`cdd`, days) | DERIVED | longest run of days < 1 mm ending at `asOf` (current dry spell), per district → area-weighted. Annual CDD from `4_2.CDD_Days`. |
| SPI-6 (`spi`) | LIVE→aggregated | `6.2.SPI_6_Medium_Term_Hydrological_Impacts`, latest month; area-weighted. `status` = drought class. |
| SPEI-6 (`spei`) | LIVE→aggregated | `meteorological_drought_master.spei_6` (one per month) — `6.2` has only SPEI-3/12. |
| Active Rainfall Stations (`activeStations`) | **UNAVAILABLE — not in DB** | No station data. (Could show "52 districts reporting" — not the same metric; not served.) |

### Time series (`SeriesSet`)

| Key | Status | Source → transformation |
| --- | --- | --- |
| `monthly` | DERIVED ⚑D3 | x = Jan…Dec; series 2026, 2025, 2024 = monthly totals; "1981–2010 mean" → **DB climatology** (`P − anomaly`), renamed "2000–2026 mean". |
| `anomaly` | DERIVED | monthly anomaly % of the aggregated series, split into positive / negative series. |
| `spi-spei` | LIVE | SPI-1/3/6/12 from `6.2`; SPEI-6 from `meteorological_drought_master.spei_6`; SPEI-12 from `6.2.SPEI_12_Annual_Water_Balance_Drought`. Bands from drought thresholds. |
| `intensity` | DERIVED | per month: max daily rainfall; R20mm days (district mean). |
| `dry-wet` | DERIVED | annual CDD / CWD from `4_2` (full years only). |
| `station` (needs `entityId`) | LIVE (district) ⚑D1 | district monthly rainfall + SPI-3 + SPI-12. Unknown id → 404. |

### Forecast — all **UNAVAILABLE** (`rainfall`, `spi`): no forecast tables.

### Breakdown (`CategorySet`)

| Key | Status | Source → transformation |
| --- | --- | --- |
| `seasonal` | DERIVED | DJF/MAM/JJA/SON totals from `4_3.Precip_*_mm` per province (9 categories; the mock uses 7 — category set to confirm with frontend). |
| `events` | DERIVED | count of district-days with daily rainfall > 50 mm per year, last 10 years. |

### Tables (`TableSet`)

| Key | Status | Source → transformation |
| --- | --- | --- |
| `provinces` | DERIVED ⚑D2 | per province: 12 m total, anomaly %, status (anomaly class). |
| `extremes` | DERIVED | districts ranked by 12 m total; Location = district, Province from lookup. |
| `statistics` | DERIVED ⚑D3 | rows: annual total, rainy days, R20, Rx1day, CDD, SPI-12 — value (2025), climatology (2000–2025), anomaly, Sen's slope (computed with `pymannkendall` on 2000–2025 annual values; `4_3.MK_Sens_Slope_mm_yr` used for MAP). |
| `sources` | PARTIAL | rows from the registry: `6.1` (columns `CHIRPS_…`, `TAMSAT_…`, `Gridded_Rainfall_Product_1km_mm`), resolution "district, daily", cadence. "Stations" column = null (unavailable). |

### Map layers (`MapLayer`) — all ⚑D1 (district choropleth instead of grid)

| Key | Status | Source |
| --- | --- | --- |
| `total` | DERIVED | 12 m total per district |
| `anomaly` | DERIVED | 12 m anomaly % |
| `monthly` | LIVE | latest `Monthly_Total_Precipitation_mm` |
| `map` | DERIVED ⚑D3 | mean annual precipitation = mean of `4_3.MAP_mm` over 2000–2025 (title baseline changes) |
| `spi1`, `spi3`, `spi6`, `spi12` | LIVE | `6.2` latest month |
| `spei6` | LIVE | `meteorological_drought_master.spei_6` latest month |
| `cdd` | DERIVED | current dry spell length |
| `r20` | LIVE | `4_2.R20mm_Days`, 2025 |
| `municipal-total` | DERIVED | same as `total` (district level; local municipalities unavailable) |
| `forecast-1`, `forecast-2`, `forecast-3` | **UNAVAILABLE** | no forecasts |

### Entities: 52 districts (`type: "district"`) ⚑D1. `entities/{id}` card fields: province, latest monthly rainfall, 12 m total, anomaly, SPI-3/6/12, drought class, last update.

---

## 2. Drought — `drought` (DB Module 1; spec §3)

Tables: `meteorological_drought_master` (daily rows; SPI/SPEI 1–24 repeated monthly), `satellite_remote_sensing`
(monthly, to 2025-12), `hydrological_drought_master` (monthly, to 2025-12).

### Summary

| KPI | Status | Source → transformation |
| --- | --- | --- |
| Drought Class (`droughtClass`) | DERIVED | class of aggregated SPI-6 (`thresholds.json`); `text` = "D1", `status` on scale `drought`. KpiSet `components` gives each KPI source's date (SPI 2026-09, VCI / dams / SGI 2025-12). |
| SPI-3, SPI-6 (`spi`) | LIVE→aggregated | `spi_3`, `spi_6` latest month |
| SPEI-12 (`spei`) | LIVE→aggregated | `spei_12` |
| VCI (`vci`, %) | LIVE→aggregated | `satellite_remote_sensing.vci` (latest 2025-12: KPI `asOf` 2025-12-01, `isStale: true`) |
| Soil Moisture Anomaly (`soilMoistureAnomaly`, %) | DERIVED | `surface_soil_moisture` vs same-month 2001–2025 mean, % |
| SSI-3 (`ssi`) | **UNAVAILABLE** (D5) | `ssi_dimensionless` is computed from `river_discharge_m3_s`, which is identical for all 52 districts every month (placeholder). Scale (SSI-1/3/…) also unspecified. |
| Dam Storage (`storagePct`, % FSC) | DERIVED | Σ `dam_storage_million_m3` / Σ (`dam_storage_million_m3` / `dam_level_pct_fsc` × 100). National 2025-12: 63.6 %. District aggregates, not per dam. |
| SGI (`sgi`) | LIVE→aggregated | `sgi_dimensionless` (to 2025-12) |
| Settlements at Risk (`settlementsAtRisk`) | **UNAVAILABLE — not in DB** | |

### Time series

| Key | Status | Source → transformation |
| --- | --- | --- |
| `spi` | LIVE | SPI-3 monthly |
| `rain-spei` | DERIVED ⚑D3 | monthly rainfall (`monthly_rainfall_mm_month`), long-term mean (DB climatology), SPEI-3 |
| `rain-anomaly` | DERIVED | anomaly % of aggregated monthly rainfall |
| `dam-flows` | DERIVED | storage % (as above); inflow / outflow Mm³/month = m³/s × seconds in month / 10⁶ from `dam_inflow_m3_s`, `dam_outflow_m3_s` |
| `gw-sgi` | PARTIAL | SGI LIVE; "mean borehole level (m bgl)" **UNAVAILABLE**: `borehole_water_level_m_bgl` ranges −0.18 … +0.20, which is not a depth below ground (looks like an anomaly). |
| `soil-moisture` | DERIVED | surface & root-zone from `satellite_remote_sensing`; min–max band and long-term mean per calendar month (2001–2025) |
| `vegetation` | LIVE | NDVI, VCI, VHI from `satellite_remote_sensing` (same source as the vegetation page) |
| `drought-area` | DERIVED ⚑D2 | % of area per class D0–D4 per month, from district SPI-6 classes and district areas |
| `storage-vs-spi` | **UNAVAILABLE** | needs individual dams (large/medium/small) |
| `population-at-risk` | **UNAVAILABLE** | no population data |
| `recovery-province` | **UNAVAILABLE** | probabilistic outlook |

### Forecast — all **UNAVAILABLE** (`spi`, `area`, `settlements`, `population`).

### Breakdown

| Key | Status | Source |
| --- | --- | --- |
| `severity` (km² per class) | DERIVED ⚑D2 | Σ district area by SPI-6 class |
| `province-area` (% in D1–D4) | DERIVED ⚑D2 | per province |
| `restrictions` | **UNAVAILABLE** | no restriction data |
| `class-probability` | **UNAVAILABLE** | forecast |

### Tables

| Key | Status | Source → transformation |
| --- | --- | --- |
| `cascade` | PARTIAL | Stage rows: Meteorological (SPI-6, to 2026-09) LIVE; Agricultural (VCI, soil-moisture anomaly, to 2025-12) LIVE; Hydrological (dam storage %, to 2025-12) LIVE, SSI unavailable; Socio-economic (settlements) null + `unavailable`. Each stage's date in `components` and a per-row `asOf` cell. |
| `affected` | PARTIAL | per province: SPI-6, class, area affected % (⚑D2), last update = `asOf`; Confidence = null (unavailable). |
| `early-warning` | **UNAVAILABLE** | forecast columns |
| `sources` | LIVE | from `/meta`: table, cadence, row count, newest date, status (Online if newest date within expected cadence, else Delayed) |
| `municipalities` | PARTIAL | per district: SPI-6, class; Population affected and Water supply risk = null (unavailable) |
| `forecast-indicators`, `recovery`, `forecast-kpis` | **UNAVAILABLE** | |

### Matrix

| Key | Status | Source |
| --- | --- | --- |
| `veg-heatmap` (9 provinces × 12 months, VCI %) | DERIVED ⚑D2 | `satellite_remote_sensing.vci`, last 12 months |
| `province-risk` | **UNAVAILABLE** | probabilities |

### Map layers ⚑D1

| Key | Status | Source |
| --- | --- | --- |
| `drought-class`, `municipal-class` | DERIVED | SPI-6 class per district (choropleth, scale `drought`) |
| `province-class` | DERIVED ⚑D2 | class of area-weighted SPI-6 per province (`level: province`) |
| `catchment-class` | **UNAVAILABLE** ⚑D2 | needs district × catchment overlap |
| `spi1`, `spi3`, `spi6`, `spi12`, `spi24`, `spei3`, `spei12` | LIVE | latest month |
| `rain-anomaly` (3 months) | DERIVED | 3-month total vs normal |
| `soil-moisture`, `ndvi`, `vci` | LIVE | `satellite_remote_sensing`, 2025-12 (`isStale`) |
| `dams`, `boreholes`, `settlements` (points) | **UNAVAILABLE** | no coordinates / individual sites |
| `outlook-1..3`, `forecast-6` | **UNAVAILABLE** | forecasts |

### Note — can `hydrological_drought_master` serve other modules?

| Module | Verdict |
| --- | --- |
| `dams` | **Partial, district-aggregated only.** Storage (Mm³), %FSC, inflow, outflow per district per month, to 2025-12. No individual dams, names, capacities, coordinates → no `entities`, `top10`, `lowest`, points maps. Could feed national/province storage KPIs and trends once the data team confirms the numbers (national storage 17.7 bn m³ at 63.6 % FSC in 2025-12 implies FSC ≈ 27.8 bn m³; DWS reports ≈ 32 bn m³ for major dams). |
| `surface-water` | **No.** `river_discharge_m3_s` (0.01–0.12 m³/s), `surface_runoff_m3_s` and `baseflow_separation_m3_s` hold the same value for all 52 districts each month; `fdc_percentiles_m3_s` is the constant 0.01. |
| `groundwater` | **SGI only**, per district. `borehole_water_level_m_bgl` is not a depth (−0.18 … +0.20). No boreholes. |

---

## 3. Climate — `climate` (DB Module 2; spec §4)

Tables: `4_1_temperature_change_master` (daily; temperatures identical to `meteorological_drought_master`),
`4_2_wet_dry_spells_master` (annual ETCCDI indices), `4_3_mean_annual_precipitation_master` (annual),
`4_4_potential_evaporation_daily_master`, `4_5_irrigation_demand_daily_master` (daily),
`4_6_streamflow_and_climate_linkage_annual _master` (annual).

### Summary

| KPI | Status | Source → transformation |
| --- | --- | --- |
| Temperature Anomaly (`tempAnomaly`, °C) | DERIVED ⚑D3 | mean of monthly `Temperature_Anomaly_C` over last 12 months, area-weighted |
| Warming Trend (`tempTrend`, °C/decade) | DERIVED | Sen's slope of annual mean `Tavg_C`, full years 2000–2025, × 10; Mann-Kendall p in `sublabel` |
| Rainfall Change (`rainfallAnomaly`, %) | DERIVED ⚑D3 | last full year `4_3.MAP_mm` vs 2000–2025 mean |
| Reference ET₀ annual (`et0`, mm) | DERIVED | Σ `meteorological_drought_master.et0_mm_day` over the last 12 months (plausible 1–7 mm/day). `4_4.Daily_ET0_mm` is 23–97× smaller and is not used. |
| Mean Annual Runoff (`runoffAnomaly`, %) | DERIVED | last full year's runoff depth (`runoff_variables_master`) vs 2000–2025 mean. `4_6` is withheld (D5). |
| Hot Days > 35 °C (`hotDays`) | LIVE | `4_1.Heatwave_Frequency_Days` (= count of days Tmax > 35 °C in the year; verified) |
| Heavy Rain Days (`heavyRainDays`) | LIVE | `4_2.R20mm_Days` |
| Consecutive Dry Days (`cdd`) | LIVE | `4_2.CDD_Days` |

### Time series

| Key | Status | Source → transformation |
| --- | --- | --- |
| `temperature-long` | DERIVED ⚑D3 | monthly mean Tmax/Tavg/Tmin from `4_1`; anomaly from `Temperature_Anomaly_C` (baseline = 2000–2026 mean, not 1981–2010) |
| `national-trend` | DERIVED | annual Tavg; 5-year moving average; Sen's-slope trend line ± 1 s.d. of residuals |
| `projections` | **UNAVAILABLE** | no climate projections (RCP) |
| `extremes` | LIVE | `4_2`: CDD_Days, CWD_Days, Rx1day_mm, Rx5day_mm |
| `heat-days` | LIVE + DERIVED | `Heatwave_Frequency_Days` per year + Sen's-slope trend |
| `extreme-rain` | LIVE + DERIVED | `R20mm_Days` + trend |
| `evaporation` | PARTIAL | ET₀ (`et0_mm_day`) and PET (`pet_mm_day`) annual sums from `meteorological_drought_master`; AET (water balance) from `runoff_variables_master.Actual_ET_from_Catchment_mm`; Pan evaporation and VPD **unavailable** (`4_4` withheld). |
| `irrigation` | **UNAVAILABLE** (D5) | `4_5` values are the same tiny magnitude as `4_4` (CWR = 0.85 × the suspect ET₀); `Soil_Moisture_Deficit_m3m3` is m³/m³ but the chart axis is mm/yr |
| `streamflow-linkage` | DERIVED | annual runoff depth (mm/yr) and anomaly from `runoff_variables_master` (`4_6` withheld) |
| `drought-frequency` | DERIVED | months per year with SPI-3 ≤ −1.5 (`6.2`), + trend |
| `indicator-trends` | DERIVED | % change vs 2020 of annual Tavg, rainfall, hot days, drought months, R20 days |

### Breakdown

| Key | Status | Source |
| --- | --- | --- |
| `seasonal-change` | PARTIAL | seasonal mean Tavg for 2001–2020 and 2021–2026; the 1981–2000 series = null (no data) |
| `map-seasonality` | LIVE | `4_3`: MAP_mm, Precip_DJF/MAM/JJA/SON_mm (category set to confirm) |
| `risk-index`, `projection-bars` | **UNAVAILABLE** | |

### Tables

| Key | Status | Source |
| --- | --- | --- |
| `statistics` | DERIVED | warming trend, hot-day trend, MAP trend (`MK_Sens_Slope_mm_yr`, `MK_Trend`), ENSO correlation (`ENSO_Nino34_Correlation`), CDD … |
| `provinces` | DERIVED ⚑D2 | temp change, rainfall change, trend, hot days, trend class |
| `signal-chain` | DERIVED | temperature → ET₀ → rainfall → runoff changes, severity from thresholds |
| `forecast-summary`, `risk-outlook` | **UNAVAILABLE** | projections |

### Map layers ⚑D1

| Key | Status | Source |
| --- | --- | --- |
| `temp-trend` | DERIVED | Sen's slope per district, °C/decade (title says "1981–present" → 2000–2025) |
| `temp-anomaly` | DERIVED | last 12 months |
| `mean-temp` | DERIVED | mean annual Tavg |
| `rain-trend` | DERIVED ⚑D3 | last decade vs earlier years (no 1981–2010) |
| `hot-days`, `cdd` | LIVE | 2025 values |
| `et0` | DERIVED | from `meteorological_drought_master.et0_mm_day` |
| `vulnerability`, `proj-temp`, `proj-rain`, `proj-spi`, `proj-availability` | **UNAVAILABLE** | |

---

## 4. Vegetation — `vegetation` (DB Module 3; spec §5)

Source (D4): **`satellite_remote_sensing`** (monthly, 2001-01 → 2025-12; same source as the drought page, so the same
district-month never shows two values). `5.1_primary_satellite_vegetation_variables_sansa_master` failed the
plausibility check and is withheld entirely (`DATA_GAPS.md` §5). Drivers from `5.2` (GDD) and the canonical rainfall /
temperature / ET₀ tables. Everything from the satellite table is ≥ 9 months old → `isStale: true`.

### Summary

| KPI | Status | Source → transformation |
| --- | --- | --- |
| NDVI mean (`ndvi`) | LIVE→aggregated | `ndvi`, latest month |
| VCI, VHI, TCI (`vci`, `vhi`, `tci`, %) | LIVE→aggregated | `vci`, `vhi`, `tci` |
| LST (`lst`, °C) | LIVE→aggregated | `lst` |
| Soil Moisture surface (`soilMoisture`) | LIVE→aggregated | `surface_soil_moisture` |
| Area Under Stress (`vegStressArea`, %) | DERIVED ⚑D2 | share of area with VHI < 40 % |
| Vegetation Anomaly (`vegAnomaly`, %) | DERIVED | NDVI vs same-month 2001–2025 mean, % |

### Time series

| Key | Status | Source |
| --- | --- | --- |
| `indices` | PARTIAL | NDVI LIVE; EVI, SAVI, MSAVI2 **unavailable** (only in withheld `5.1`) → returned as `null` series entries + `unavailable` |
| `index-trend` | DERIVED | ±1 s.d. band per calendar month (2001–2025, not 1991–2020), current year, previous year |
| `anomaly` | DERIVED | NDVI anomaly % |
| `drivers` | LIVE | monthly rainfall from `6.1` |

### Forecast — **UNAVAILABLE** (`ndvi`, `recovery-probability`).

### Breakdown

| Key | Status | Source |
| --- | --- | --- |
| `land-cover` | **UNAVAILABLE** | no land-cover data per district |
| `classification` | DERIVED ⚑D2 | % area per VHI class (`vegClass` thresholds) |
| `by-province` | DERIVED ⚑D2 | class shares per province |
| `seasonal` | DERIVED | mean NDVI per season per category (category set to confirm) |
| `drivers` | DERIVED | change vs normal over 6 months: rainfall (`6.1`), mean temperature (`4_1`), ET₀ (`meteorological`), GDD (`5.2`), LST (`satellite`) — each with its own `asOf` in `components` |

### Tables

| Key | Status | Source |
| --- | --- | --- |
| `provinces` | DERIVED ⚑D2 | mean NDVI, VCI, health class, change % |
| `hotspots` | DERIVED | districts ranked by NDVI change / VCI (Municipality = district) |
| `forecast-indicators` | **UNAVAILABLE** | |

### Map layers — latest month, district choropleth

| Key | Status | Note |
| --- | --- | --- |
| `ndvi`, `vci`, `vhi`, `tci`, `ndwi`, `lst`, `sm` | LIVE | `satellite_remote_sensing` (2025-12, `isStale`) |
| `ndvi-anomaly` | DERIVED | vs same month 2001–2025 (title says 1981–2010 → `baseline` + note) |
| `ndvi-trend` | DERIVED | Sen's slope per decade, 2001–2025 |
| `evi`, `savi`, `msavi2`, `vpi`, `asi`, `svi`, `ndii` | **UNAVAILABLE** | only in the withheld `5.1` source |
| `forecast-ndvi`, `forecast-anomaly` | **UNAVAILABLE** | |

---

## 5. Runoff — `runoff` (DB Module 5; spec §7)

Table: `runoff_variables_master` (daily, complete calendar, PK (`District_Name`, `Date`)).
The table is **modelled, not gauged**: `Daily_Surface_Runoff_Volume_m3_day` = rainfall × 6 500 km² × runoff
coefficient for every district (verified to < 0.1 %); `Catchment_Area_km2` (6 500), `Curve_Number_CN` (72.9),
`Impervious_Surface_Fraction_percent` (4.5) and `Dominant_LandCover_Class` are single constants. Runoff **depth**
(mm = volume / area used) is internally consistent; **volumes and flows (m³, m³/s) are not** real district values
because every district is given the same area. Per D2/D5 the API recovers depth (volume / 6 500 km²) and re-derives
**volume = depth × real district area** (labelled derived); the table's own volumes, peak flows, catchment area and
other constants are withheld.

### Summary

| KPI | Status | Source → transformation |
| --- | --- | --- |
| Annual Runoff (`runoff`, mm) | DERIVED | Σ daily volume / (6 500 km²) over 12 months → mm, area-weighted |
| Runoff Anomaly (`runoffAnomaly`, %) | DERIVED | 12 m runoff vs same-period 2000–2025 mean |
| Peak Flow Qmax (`peakFlow`, m³/s) | **UNAVAILABLE** (D5) | `Peak_Flow_Rates_m3s` is scaled to the constant 6 500 km² area |
| Baseflow Index (`baseflowIndex`) | DERIVED | Σ `Baseflow_Component_m3s` / Σ (model volume / 86 400) — a ratio, so the shared area scaling cancels |
| Runoff Coefficient (`runoffCoefficient`) | DERIVED | mean `Runoff_Coefficient_dimensionless` (equivalently Σ runoff / Σ rainfall) |
| SSI-6 (`ssi`) | DERIVED | computed in `core/indices.py`: 6-month runoff totals, gamma fit per calendar month over 2000–2025 (documented; 26 years < WMO-recommended 30) |
| Stations with Normal / Low Flow (`stationsHighFlow`, `stationsLowFlow`, %) | **UNAVAILABLE — not in DB** | no gauging stations |

### Time series

| Key | Status | Source |
| --- | --- | --- |
| `discharge` | DERIVED ⚑D2 | daily mean flow = derived volume (depth × real area) / 86 400 (m³/s); monthly mean; long-term mean 2000–2025 |
| `runoff-anomaly` | DERIVED | monthly anomaly % + 12-month moving average |
| `ssi` | DERIVED | SSI-1/3/6/12 computed |
| `rainfall-runoff` | LIVE | monthly Σ `Mean_Catchment_Rainfall_mm` and runoff mm |
| `water-balance` | LIVE | monthly P, AET (`Actual_ET_from_Catchment_mm`), Q (mm) |
| `flood-frequency` | DERIVED ⚑D2 | annual maxima of derived **daily mean** flow (2000–2025; not instantaneous peaks — labelled), Log-Pearson III fit + 90 % CI |
| `fdc` | DERIVED ⚑D2 | flow-duration curves of derived daily flow: last 12 m, previous 12 m, 2011–2020 |
| `seasonal` | DERIVED ⚑D2 | mean derived flow per calendar month: 2026, 2025, long-term |
| `national-volume` | DERIVED ⚑D2 | Σ derived district volumes (Mm³) per month |
| `drivers` | LIVE | catchment rainfall |

### Forecast — **UNAVAILABLE** (`runoff`, `flow`, `flood-probability`).

### Breakdown

| Key | Status | Note |
| --- | --- | --- |
| `by-province` (stations by flow status) | **UNAVAILABLE** | no stations |
| `by-wma` (Mm³) | DERIVED ⚑D2 | derived volumes apportioned by district–WMA overlap area (`district-overlap`) |

### Tables

| Key | Status | Source |
| --- | --- | --- |
| `statistics` | DERIVED | runoff mm, coefficient, BFI, peak flow — current 12 m, climatology 2000–2025, anomaly, Sen's slope |
| `top-stations` | **UNAVAILABLE** ⚑D1 | no stations (districts could be ranked instead if D1 is accepted) |
| `events` | DERIVED | days where a district's runoff depth exceeds its Q10 (high) or falls below Q90 (low) |

### Map layers ⚑D1

| Key | Status | Source |
| --- | --- | --- |
| `annual-runoff`, `runoff-anomaly`, `monthly-runoff`, `runoff-coefficient` | DERIVED | district choropleth (coefficient over 2000–2025) |
| `catchment-runoff` | **UNAVAILABLE** ⚑D2 | needs catchment overlap |
| `stations`, `flow-status`, `flow-anomaly`, `ssi` (points) | **UNAVAILABLE** | no station coordinates (district choropleth equivalents possible under D1) |

### Entities: 52 districts ⚑D1 (contract expects 1 842 gauging stations).

---

## 6. Cross-module endpoints (served by this backend)

| Endpoint | Status | Source |
| --- | --- | --- |
| `GET /api/v1/reference/provinces` | LIVE | static: 9 provinces |
| `GET /api/v1/reference/municipalities` | LIVE (districts) | Appendix A lookup (`parent` = province) |
| `GET /api/v1/reference/wmas`, `/catchments` | ⚑D2 | from `reference/geo/` |
| `GET /api/v1/search?q=` | LIVE | districts, provinces |
| `GET /api/v1/alerts` | DERIVED (later) | drought-class alerts per district from SPI-6; out of Phase 0 scope |
| `GET /api/v1/meta/sources`, `/coverage`, `/health` | LIVE | from the registry + `pg_class` / `count(*)` / `max(date)` |

---

## Appendix A — District name normalisation (52 units)

The API keys districts by **MDB code**. `province` is the contract province code. `aliases` are names found in the
database (matching is case- and space-insensitive). Codes are the Municipal Demarcation Board codes; current official
names are given where the database uses an older one. Machine-readable copy: `app/reference/districts.csv`.

| MDB code | DB name (`meteorological`, `4_*`, `5.*`, `6.*`, `runoff`; lowercase in `hydrological_*`) | Extra alias (`satellite_remote_sensing`) | Current official name | Province |
| --- | --- | --- | --- | --- |
| DC44 | Alfred Nzo | | Alfred Nzo | EC |
| DC12 | Amathole | | Amathole | EC |
| BUF | Buffalo City | | Buffalo City | EC |
| DC10 | Cacadu | sarah baartman | Sarah Baartman | EC |
| DC13 | Chris Hani | | Chris Hani | EC |
| DC14 | Joe Gqabi | | Joe Gqabi | EC |
| NMA | Nelson Mandela Bay | | Nelson Mandela Bay | EC |
| DC15 | O.R.Tambo | ortambo | O.R. Tambo | EC |
| DC20 | Fezile Dabi | | Fezile Dabi | FS |
| DC18 | Lejweleputswa | | Lejweleputswa | FS |
| MAN | Mangaung | | Mangaung | FS |
| DC19 | Thabo Mofutsanyane | | Thabo Mofutsanyana | FS |
| DC16 | Xhariep | | Xhariep | FS |
| JHB | City of Johannesburg | johannesburg | City of Johannesburg | GP |
| TSH | City of Tshwane | tshwane | City of Tshwane | GP |
| EKU | Ekurhuleni | | City of Ekurhuleni | GP |
| DC42 | Sedibeng | | Sedibeng | GP |
| DC48 | West Rand | | West Rand | GP |
| DC25 | Amajuba | | Amajuba | KZN |
| ETH | eThekwini | | eThekwini | KZN |
| DC29 | iLembe | | iLembe | KZN |
| DC43 | Sisonke | | Harry Gwala | KZN |
| DC21 | Ugu | | Ugu | KZN |
| DC22 | Umgungundlovu | | uMgungundlovu | KZN |
| DC27 | Umkhanyakude | | uMkhanyakude | KZN |
| DC24 | Umzinyathi | | uMzinyathi | KZN |
| DC23 | Uthukela | | uThukela | KZN |
| DC28 | Uthungulu | | King Cetshwayo | KZN |
| DC26 | Zululand | | Zululand | KZN |
| DC35 | Capricorn | | Capricorn | LP |
| DC33 | Mopani | | Mopani | LP |
| DC47 | Sekhukhune | | Sekhukhune | LP |
| DC34 | Vhembe | | Vhembe | LP |
| DC36 | Waterberg | | Waterberg | LP |
| DC32 | Ehlanzeni | | Ehlanzeni | MP |
| DC30 | Gert Sibande | | Gert Sibande | MP |
| DC31 | Nkangala | | Nkangala | MP |
| DC9 | Frances Baard | | Frances Baard | NC |
| DC45 | John Taolo Gaetsewe | | John Taolo Gaetsewe | NC |
| DC6 | Namakwa | | Namakwa | NC |
| DC7 | Pixley ka Seme | | Pixley ka Seme | NC |
| DC8 | Z F Mgcawu | | ZF Mgcawu | NC |
| DC37 | Bojanala | | Bojanala Platinum | NW |
| DC40 | Dr Kenneth Kaunda | | Dr Kenneth Kaunda | NW |
| DC39 | Dr Ruth Segomotsi Mompati | | Dr Ruth Segomotsi Mompati | NW |
| DC38 | Ngaka Modiri Molema | | Ngaka Modiri Molema | NW |
| DC2 | Cape Winelands | | Cape Winelands | WC |
| DC5 | Central Karoo | | Central Karoo | WC |
| CPT | City of Cape Town | cape town | City of Cape Town | WC |
| DC4 | Eden | | Garden Route | WC |
| DC3 | Overberg | | Overberg | WC |
| DC1 | West Coast | | West Coast | WC |

Counts: EC 8 · FS 5 · GP 5 · KZN 11 · LP 5 · MP 3 · NC 5 · NW 4 · WC 6 = 52.
