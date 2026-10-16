# Data gaps — for the NIWIS data team

> What the NIWIS dashboard needs that the Supabase database does not provide yet, and data problems found while
> profiling it. Generated from a read-only inventory on 2026-10-14 (`docs/DB_INVENTORY.md`); every finding can be
> reproduced with `python scripts/run_checks.py scripts/data_checks.sql` (plus `ndvi_plausibility.sql` and
> `duplicate_checks.sql` for §5 and §13). Updated 2026-10-14 after the backend team's decisions D1–D5.
>
> **What the API does today:** anything listed here as withheld is *not served*; those panels return
> `DATA_UNAVAILABLE` and show as "Sample" on the dashboard until the data are fixed.
>
> Each item says **what to fix** and **what it unblocks** on the dashboard. Priorities: **P1** blocks a headline
> panel or makes a shown number wrong · **P2** blocks secondary panels · **P3** tidy-up.

## Summary

| # | Issue | Tables | Priority |
| --- | --- | --- | --- |
| 1 | All data is per **district municipality**: no stations, coordinates, catchment or WMA codes | all 14 | P1 |
| 2 | Data starts in **2000** → no 1981–2010 baseline; stored anomalies use a 2000–2026 mean | all | P1 |
| 3 | Placeholder columns: same value in every district, or one constant | `hydrological_drought_master`, `5.1`, `runoff_variables_master` | P1 |
| 4 | Implausible magnitudes / units | `4_4`, `4_5`, `4_6`, `hydrological_drought_master`, `runoff_variables_master` | P1 |
| 5 | Two vegetation sources disagree; **`5.1` fails the plausibility check and is withheld** | `satellite_remote_sensing` vs `5.1` | P1 |
| 6 | 31 December missing every year | `meteorological_drought_master`, `4_1`, `4_4`, `4_5`, `5.1`, `5.2` | P2 |
| 7 | `satellite_remote_sensing`, `hydrological_drought_master` end 2025-12 (9 months behind) | 2 tables | P2 |
| 8 | District names differ between tables; several are pre-2016 names | `satellite_remote_sensing` (+ lowercase in `hydrological_drought_master`, old names everywhere) | P2 |
| 9 | Variables the dashboard needs that no table has | — | P2 |
| 10 | Table naming, types, keys and indexes | most `4_*`, `5.*`, `6.*` tables | P3 |
| 11 | Read-only access for the API | database roles / RLS | P2 |
| 12 | ~~No district boundaries~~ — resolved: official district boundaries supplied 2026-10-14 | `reference/geo/districts.json` | done |
| 13 | The same data stored in several tables; canonical source chosen per variable | 9 tables | P3 |
| 14 | Partial current month: anomaly and SPI compare a few days with a full-month normal | `6.1`, `6.2`, `meteorological`, `5.2` | P2 |
| 15 | Plausibility checks for the database view (2026-10-15): **reservoir columns, solar radiation, GRACE, the `4_3` record statistics and `4_6` fail and are withheld** | `hydrological_drought_master`, `meteorological_drought_master`, `satellite_remote_sensing`, `4_3`, `4_6` | P1 |
| 17 | Rain on the newest day in 42 of 52 districts (2026-09-06): verify the last load | `6.1` | P3 |
| 16 | 13 new tables (§8–§12: reservoirs, surface water, groundwater, water quality) appeared on 2026-10-15; not registered, not served | `8_1` … `12.4` | P2 |

---

## 1. Spatial resolution: districts only (P1)

Every table is keyed by district name (52 = 44 districts + 8 metros). There are no station IDs, latitude/longitude,
grid cells, quaternary/tertiary catchment codes or WMA codes.

**Impact:** station pages and station maps (rainfall: 213 stations; runoff/surface water: 1 842 gauges), point maps
(dams, boreholes, gauges), and filters by WMA or catchment cannot be served. The backend will show districts instead
where it can.

**Ask:**
- If station or gridded data exists upstream (SAWS stations, CHIRPS grid, DWS gauges — `6.1` already has `CHIRPS_…`,
  `TAMSAT_…` and `Gridded_Rainfall_Product_1km_mm` columns), load it at native resolution with
  `station_id` / `lat` / `lon` (or `cell_id` + centroid), so districts become an aggregate instead of the only level.
- Add an **MDB code** column (`DC44`, `CPT`, …) to every table so joins don't depend on name spelling.

## 2. Period of record and baseline (P1)

| Finding | Evidence |
| --- | --- |
| Daily/monthly tables cover 2000-01-01 → 2026-09-06 (26.7 years); `satellite_remote_sensing` and `hydrological_drought_master` 2001-01 → 2025-12 | inventory |
| The spec and dashboard use a **1981–2010** baseline for anomalies, SPI calibration and "long-term mean" panels | contract titles, spec §3–§7 |
| Stored anomalies (`rainfall_anomaly_mm/_pct`, `Monthly_Anomaly_mm/_percent`, `temp_anomaly_c`, `Temperature_Anomaly_C`) are relative to the **mean of all years 2000–2026** for each calendar month (implied normal matches that mean to 3 decimals, s.d. 0.0003) | checks "6.1 anomaly baseline", "temperature anomaly baseline" |
| SPI/SPEI calibration period is not documented | — |

**Ask:** (a) load data back to at least 1981, or (b) confirm the intended baseline (e.g. 2000–2025 full years) so
the dashboard can label it correctly; and document the SPI/SPEI distribution and calibration period. Note: including
the partial year 2026 in the climatology changes every anomaly each time new data are loaded.

## 3. Placeholder and constant columns (P1)

These columns carry no information; the API treats them as **unavailable** until fixed.

| Table | Column | Problem |
| --- | --- | --- |
| `hydrological_drought_master` | `river_discharge_m3_s`, `surface_runoff_m3_s`, `baseflow_separation_m3_s` | Same value for all 52 districts in every month (e.g. 0.0393884876 m³/s in 2025-12); 0.01–0.12 m³/s is far too low for district river flow |
| `hydrological_drought_master` | `ssi_dimensionless` | Derived from the placeholder discharge above (range −0.51 … +6.6) |
| `hydrological_drought_master` | `fdc_percentiles_m3_s` | Constant 0.01 |
| `meteorological_drought_master` | `wind_speed_2m_ms` | Constant 2.5 (affects any ET₀ computed from it) |
| `5.1_primary_satellite_vegetation_variables_sansa_master` | `Surface_Soil_Moisture_m3m3` | Constant 0.22 |
| `5.1_…` | `VPI_percent` | Exact copy of `VCI_percent` in all 505 440 rows |
| `5.1_…` | `ASI_percent` | Exactly 100 − `VHI_percent` — confirm this is the intended definition |
| `runoff_variables_master` | `Catchment_Area_km2` (6 500), `Curve_Number_CN` (72.9), `Impervious_Surface_Fraction_percent` (4.5), `Dominant_LandCover_Class` | One constant for every district and day |

## 4. Implausible magnitudes and units (P1)

| Table · column | Finding | Expected | Ask |
| --- | --- | --- | --- |
| `4_4_potential_evaporation_daily_master` · `Daily_ET0_mm`, `Daily_PET_mm`, `Daily_AET_mm`, `Daily_Pan_Evap_mm` | 0.02–0.28 mm/day; `meteorological_drought_master.et0_mm_day` is **23–97× larger** for the same district-day (not a constant unit factor); Jan 2024 Alfred Nzo: Σ 3.5 mm vs 125.7 mm in `5.2` | ET₀ 1–8 mm/day in SA | Recompute or document the unit |
| `4_5_irrigation_demand_daily_master` · `CWR_mm_day`, `Irrigation_Water_Demand_mm`, `Rainfall_Deficit_mm`, `Irrigation_Return_Flow_mm` | Built on the `4_4` values (CWR = 0.85 × that ET₀), so the same magnitude problem | | Recompute after `4_4` |
| `4_5` · `Soil_Moisture_Deficit_m3m3` | Volumetric (m³/m³), but the irrigation panel needs a depth (mm) | | Add mm or root-zone depth |
| `4_4` · `SPEI_Daily` | Stored as **text**; 0.3 % null | numeric | Change type to `double precision` |
| `4_6_streamflow_and_climate_linkage_annual _master` · `MAR_m3_year` | National sum ≈ 332 000–432 000 Mm³/yr | SA total MAR ≈ 49 000 Mm³/yr | Check unit / area basis |
| `4_6` · `Rainfall_Runoff_Ratio` | 0.49–0.98 | SA runoff coefficients typically 0.02–0.2 (`runoff_variables_master` uses 0.10–0.26) | Check definition |
| `4_6` · `Q90_LowFlow_m3_year`, `Q10_FloodFlow_m3_year` | One value per district repeated for all years | | Confirm these are long-term statistics, or compute per year |
| `4_3_mean_annual_precipitation_master` · `Nino34_Annual_Mean` | 2026 = −41.28 (Niño 3.4 anomalies are within about ±3 °C) | | Fix 2026 value |
| `4_3` · `MK_Sens_Slope_mm_yr`, `MK_p_value`, `MK_Trend`, `ENSO_Nino34_Correlation`, `Precip_CV_Percent` | Whole-record statistics repeated on every year row | | Fine, but better in a per-district table |
| `hydrological_drought_master` · `borehole_water_level_m_bgl` | −0.18 … +0.20 — not a depth below ground level | 1–100+ m bgl | Provide depth to water level; label anomalies as such |
| `hydrological_drought_master` · `dam_storage_million_m3`, `dam_level_pct_fsc` | National total 17 715 Mm³ at implied 63.6 % FSC (2025-12) → implied capacity ≈ 27 800 Mm³; DWS major-dam capacity is ≈ 32 000 Mm³ | | Confirm which dams are included; provide per-dam data (see §9) |
| `runoff_variables_master` · `Daily_Surface_Runoff_Volume_m3_day`, `Monthly_Runoff_Totals_Million_m3_month`, `Peak_Flow_Rates_m3s`, `Baseflow_Component_m3s` | Volume = rainfall × **6 500 km²** × runoff coefficient for every district (verified < 0.1 %). Depth (mm) is consistent, but volumes and flows are wrong for any district whose area isn't 6 500 km² | real district or catchment areas | Use real areas; if the table is modelled, say so in a table comment |
| `meteorological_drought_master` · `spi_*`, `spei_*` (and copies) | Capped at +3.0; SPI-1 never below −1.51 | | Document capping / fitting method |

No table or column has a comment today. A short **column dictionary** (`COMMENT ON TABLE` / `COMMENT ON COLUMN`
with unit, method and source per column, and whether the table is observed or modelled) would remove most of the
questions above.

## 5. Vegetation: `5.1` fails the plausibility check — withheld (P1)

The two NDVI sources disagree (Alfred Nzo, Jan 2024: **0.677** in `satellite_remote_sensing`, **0.115** in `5.1`).
The same district and month must never show different values on different pages, so both were tested against what
NDVI must look like in South Africa (`scripts/ndvi_plausibility.sql`, common period 2001–2025):

| Test | Expected | `satellite_remote_sensing` | `5.1_…_sansa_master` |
| --- | --- | --- | --- |
| Arid west, mean NDVI | ≈ 0.10–0.25 | Namakwa **0.17**, Central Karoo 0.18, ZF Mgcawu 0.19, Pixley ka Seme 0.22 ✅ | 0.35 in every district ❌ |
| Wet east, wet season (DJF) | high, above the dry season | eThekwini 0.68 / 0.50 (DJF / JJA), Ugu 0.74 / 0.56, Ehlanzeni 0.64 / 0.43 ✅ | DJF **0.13**, JJA **0.57** in every district — inverted ❌ |
| Seasonal cycle follows the rainfall regime | peak 1–2 months after the rainy season | Summer-rainfall districts peak Jan–Apr; winter-rainfall Western Cape (Cape Town, Overberg, West Coast, Cape Winelands) peak **July** ✅. Climatology vs previous-month rainfall: mean r = **0.87**; r ≥ 0.5 in **48 / 52** districts | Same cycle everywhere, peak June/July, identical amplitude 0.48. Mean r = **−0.58**; r ≥ 0.5 in 4 / 52 (only the winter-rainfall districts, by coincidence) ❌ |
| Wettest vs driest districts | east high, Karoo low | top Ugu 0.66, iLembe 0.65; bottom Namakwa 0.17, Central Karoo 0.18 ✅ | all 52 districts 0.35 ❌ |
| Year-to-year response to rain | NDVI anomaly follows the 3-month rainfall anomaly | r = **0.63** ✅ | r = −0.03 ❌ |

The only weak cases for `satellite_remote_sensing` are districts with all-year or bimodal rainfall (Eden, Nelson
Mandela Bay) and the Karoo (Cacadu, Central Karoo), where a weak seasonal cycle is expected.

**Outcome (applied in the API):** `satellite_remote_sensing` is the single vegetation source for **both** the drought
and vegetation pages. The whole vegetation part of `5.1` is withheld: its NDVI fails; EVI, SAVI and NDII are perfectly
correlated with that NDVI (r = 1.000); VPI is an exact copy of VCI; ASI = 100 − VHI; soil moisture is the constant
0.22. Indices that only `5.1` provides (EVI, SAVI, MSAVI2, VPI, ASI, SVI, NDII) are therefore unavailable.

`5.1.NDVI_10day_composite` also changes every day (up to 31 values per month), so it is not a 10-day composite.

**Ask:** fix or replace `5.1` (the cycle looks like one synthetic curve applied to every district); extend
`satellite_remote_sensing` beyond 2025-12 (§7), and add EVI/SAVI/MSAVI2 there if the dashboard should show them.

## 6. Missing 31 December (P2)

`meteorological_drought_master`, `4_1`, `4_4`, `4_5` and `5.1` have no row for **31 December in any year**
(9 720 dates instead of 9 746). `5.2.Monthly_Rainfall_mm` differs from `6.1` only in December (1 206 district-months,
up to 34 mm), consistent with the missing day. `6.1` and `runoff_variables_master` are complete.
**Ask:** reload 31 December.

## 7. Currency (P2)

`satellite_remote_sensing` and `hydrological_drought_master` end **2025-12-01**; everything else reaches
2026-09. Drought-page VCI, soil moisture, dam storage and SGI will show "as of Dec 2025". **Ask:** extend to the
current month, and agree an update cadence per table (daily / monthly) so the API can flag late feeds.

## 8. District names (P2)

- `satellite_remote_sensing` uses lowercase names, and five use different spellings: `cape town`, `johannesburg`,
  `tshwane`, `ortambo`, `sarah baartman` (other tables: `City of Cape Town`, `City of Johannesburg`,
  `City of Tshwane`, `O.R.Tambo`, `Cacadu`). `hydrological_drought_master` uses lowercase versions of the standard
  names (`city of cape town`, `o.r.tambo`, …).
- All tables use some **pre-2016 names**: `Cacadu` (now Sarah Baartman), `Eden` (Garden Route), `Sisonke`
  (Harry Gwala), `Uthungulu` (King Cetshwayo); `Bojanala` (Bojanala Platinum).
- The API maps all of these through an alias table (`docs/DATA_MAPPING.md`, Appendix A). Names that don't match the
  dashboard's boundary files will be listed here once `reference/geo/` is available.

**Ask:** add an `mdb_code` column (see §1) and use one spelling everywhere.

## 9. Variables the dashboard needs that no table provides (P2)

| Dashboard need (module · panel) | Missing data |
| --- | --- |
| Rainfall · active stations, station list/map | Station metadata + station observations |
| Drought · settlements / population at risk, municipal water-supply risk | Settlement register with population, location, supply source (spec §20) |
| Drought · water restrictions by municipality | Restriction levels per municipality |
| Drought · storage vs SPI by dam size; dam points | Per-dam storage with capacity, coordinates (spec §8) |
| Drought · borehole map, mean borehole level | Per-borehole water levels (m bgl) with coordinates (spec §10) |
| Climate · projections (RCP 4.5 / 8.5), vulnerability index | Downscaled projections, vulnerability index |
| Vegetation · land-cover breakdown; surface soil moisture | Land-cover class per district/area; a real soil-moisture series in `5.1` |
| Runoff · gauging stations, flow status, station flow anomaly | DWS gauge flows with station codes and coordinates |
| Runoff/IWRM · by WMA and catchment | District × WMA / catchment area overlap, or data at catchment level |
| All modules · forecasts | No forecast tables yet (expected; the API marks forecast panels unavailable) |

## 10. Structure, types, keys and indexes (P3)

| Issue | Tables | Suggestion |
| --- | --- | --- |
| Table names with a space or dots: `4_6_streamflow_and_climate_linkage_annual _master`, `5.1_…`, `5.2_…`, `6.1_…`, `6.2_…` | 5 tables | Rename to `snake_case` without spaces or dots (the build brief expected `4_6_…_annual_master`, `5_1_…`, `6_1_…`). The API can follow either, but each rename must be coordinated. |
| Mixed column-name case (`district_name` vs `District_Name`) | Module 1 vs others | Pick lowercase `snake_case` |
| Dates stored as **text** (`Date`) | `4_1`, `4_4`, `4_5`, `5.1`, `6.1` | Change to `date` |
| Numbers stored as text: `4_4.SPEI_Daily`, `6.1.Seasonal_Total_OND_mm` | 2 tables | Change to `double precision` |
| No primary key and **no indexes** | `4_1`–`4_6`, `5.1`, `5.2`, `6.1`, `6.2` | PK (`District_Name`, `Date`) or (`District_Name`, `Year`[, `Month`]); proposed SQL in `migrations/proposed/` |
| Monthly/annual values repeated on every daily row (SPI, monthly rainfall, anomalies, heatwave counts, seasonal totals) | `meteorological_drought_master`, `4_1`, `6.1` | Move to monthly/annual tables; keeps daily tables small and avoids double counting |
| Same data duplicated across tables (`6.2` SPI = `meteorological` SPI; `6.1` daily rain = `meteorological` daily rain; `4_1` temperatures = `meteorological` temperatures) | several | Fine for now; document which table is the source of truth |
| Partial year 2026 in annual tables (`4_2`, `4_3`, `4_6`) | 3 tables | Flag partial years (e.g. `is_complete` column) |

## 11. Access for the API (P2)

- Row-level security is enabled on all 14 tables with **no policies**, so `anon`/`authenticated` see 0 rows (good).
- The API currently connects as `postgres` via the session pooler (full privileges; the API itself only issues
  read-only transactions).
- A `supabase_read_only_user` role exists but has **no grants** on these tables.
- **Ask:** create a dedicated read-only login for the API (SELECT on these tables only). Proposed SQL will be in
  `migrations/proposed/` for review — the backend team will not run it.

## 12. District boundaries — resolved 2026-10-14

The frontend's `municipalities.json` has 213 local municipalities with no district or MDB code, so districts could not
be dissolved from it. The backend team supplied official district boundaries (geoBoundaries ZAF ADM2,
`south_africa_52_areas_clean.geojson`). Validation (`scripts/check_district_boundaries.py`): 52 / 52 districts matched
by name (including the database's old names Cacadu, Eden, Sisonke, Uthungulu); no overlaps; districts tile each
province within 0.15 %; areas within 0.2 % of official figures (Cape Town 2 446 km², Namakwa 126 966 vs 126 836 km²).
A simplified copy with MDB codes is in `reference/geo/districts.json` and is used for area weights and the district–WMA
overlap. **Remaining ask (frontend):** use `districts.json` (or the same boundaries) to draw district maps keyed by MDB
code.

## 13. Duplicated data and the canonical source per variable (P3)

The same values are stored in several tables (verified with `scripts/duplicate_checks.sql`). The API reads each
variable from exactly one table (`app/registry/datasets.yaml`); the copies are never served. Please confirm the choices
and consider dropping or documenting the copies.

| Variable | Canonical (served) | Duplicates (not served) | Evidence |
| --- | --- | --- | --- |
| Daily rainfall | `6.1.Daily_Rainfall_Total_mm` | `meteorological.daily_rainfall_mm_day`, `6.1.Gridded_Rainfall_Product_1km_mm`, `runoff.Mean_Catchment_Rainfall_mm` | identical in all 505 440 shared rows; `6.1` also has 31 Dec |
| Monthly rainfall | `6.1.Monthly_Total_Precipitation_mm` | `meteorological.monthly_rainfall_mm_month`, `5.2.Monthly_Rainfall_mm` | identical except December (copies miss 31 Dec) |
| Monthly rainfall anomaly (mm, %) | `6.1.Monthly_Anomaly_mm`, `_percent` | `meteorological.rainfall_anomaly_*`, `5.2.Rainfall_Anomaly_percent` | identical except December |
| Annual rainfall | `4_3.MAP_mm` | `6.1.Annual_Total_Precipitation_mm` | identical |
| Rainfall intensity | `6.1.Hourly_Rainfall_Intensity_mm_h` | `meteorological.rainfall_intensity_mm_h` | identical |
| SPI-1/3/6/12/24 | `6.2.SPI_*` | `meteorological.spi_*`, `5.2.SPI_*_month` | identical in all 16 692 district-months |
| SPEI-1/3/6/12/24 | `meteorological.spei_*` (only complete set) | `6.2.SPEI_3_*`, `6.2.SPEI_12_*`, `5.2.SPEI_*_month` | same series, rounding differences ≤ 0.06 (r = 1.000) — confirm which precision is official |
| Tmin, Tmax, Tavg | `4_1.Tmin_C`, `Tmax_C`, `Tavg_C` | `meteorological.tmin_c`, `tmax_c`, `tavg_c`; `5.2.Mean_Temperature_C` (monthly mean) | identical |
| Temperature anomaly | `4_1.Temperature_Anomaly_C` | `meteorological.temp_anomaly_c` | differences ≤ 0.001 °C (rounding) |
| Reference ET₀ | `meteorological.et0_mm_day` | `5.2.Potential_Evapotranspiration_ET0_mm` (monthly sum) | identical sums |

`hydrological_drought_master` and `runoff_variables_master` hold *different* runoff models (monthly correlation
r = 0.61), not duplicates; the hydrology flow columns are withheld (§3).

## 14. Partial current month (P2)

The newest month in the monthly columns is incomplete (2026-09 covers 1–6 September), but it is treated as a full
month: `6.1.Monthly_Anomaly_mm` for Alfred Nzo, Sep 2026 = 31.93 mm − 55.40 mm normal = **−23.47 mm** (6 days of rain
against a 30-day normal); SPI / SPEI for 2026-09 are computed the same way, so the latest month always looks drier
than it is.

**What the API does:** uses the latest **complete** month for SPI, SPEI and monthly anomalies, and pro-rates normals
by days for window totals. **Ask:** either publish monthly indices only for complete months, or add a
`days_in_record` / `is_complete` column so consumers can tell.

## 15. Plausibility checks before serving columns in the database view (P1) — 2026-10-15

Every column becomes a chart variable in the dashboard's database view (`/api/v1/{module}/categories`,
`docs/CATEGORY_CATALOGUE.md`). Before any column not yet served went live, it was checked for physical plausibility;
so were the derived columns the dashboard now reads directly. All checks are read-only SQL against Supabase on
2026-10-15 (`scripts/plausibility_checks.sql`, runnable with `python scripts/run_checks.py scripts/plausibility_checks.sql`).
A column that fails is **withheld** everywhere (registry role in `app/registry/datasets.yaml`) with the reason below.

### Failed → withheld

| Table · column | Evidence | Effect on the dashboard |
| --- | --- | --- |
| `hydrological_drought_master` · `dam_storage_million_m3`, `dam_level_pct_fsc`, `dam_inflow_m3_s`, `dam_outflow_m3_s` | **Frozen since December 2021**: every district's storage equals its final value from 2021-12 onwards (52 / 52), so the national total stays at 17 715 Mm³ (58.9 % mean FSC) every month; inflow = outflow in 2 496 of 2 600 district-months since Nov 2021. **Before that, implausible swings**: national storage 421 Mm³ (Jun 2021) → 16 617 Mm³ (Aug 2021); mean % FSC 69 % (Jul 2002) → 0.5 % (Jul 2003); eThekwini 74.5 % → 0 % in one month (May 2016); 1 212 district-months at exactly 0 % FSC. The month-to-month change equals (inflow − outflow) × time: a self-consistent bucket model, not observed storage. | Drought: the Dam Storage KPI and the reservoir stage of the cascade are null with this reason; the dam-flows chart returns 501. |
| `meteorological_drought_master` · `solar_radiation_wm2` | 365 distinct values in 470 058 rows: one value per calendar day, **identical in all 52 districts and every year** (Cape Town = Johannesburg = Namakwa = eThekwini: January 198.8, April 247.1, September 113.2 W/m²). The cycle peaks in April and is lowest in September–October; Southern-Hemisphere radiation peaks in December and is lowest in June. r = 0.02 with Tmax. | Never served. |
| `meteorological_drought_master` · `seasonal_rainfall_mm_season` | One value per district-**year**, equal to the calendar-year total (Alfred Nzo 2014 = 825.67 mm = Σ of its 12 monthly totals, 31 Dec missing): not a seasonal total. | Never served. Seasons come from `6.1` (OND/JFM/AMJ/JAS) and `4_3` (DJF/MAM/JJA/SON). |
| `satellite_remote_sensing` · `grace_tws` | 70–412 in 2001 and up to 270 in 2002, but GRACE data start in April 2002; from 2003 the values are about ±10 and smooth (mean month-to-month step 2.6). Unit undocumented (cm, mm or anomaly?). | Never served. **Ask:** remove the values before 2002-04 and document the unit. |
| `4_3_mean_annual_precipitation_master` · `Precip_CV_Percent`, `MK_Sens_Slope_mm_yr`, `MK_p_value`, `MK_Trend`, `ENSO_Nino34_Correlation` | Whole-record statistics computed over **2000–2026, including the partial year 2026** (Alfred Nzo MAP 2026 = 547 mm for 8 months). Reproduced exactly with `app/core/stats.py` on 2000–2026 (Alfred Nzo −1.028 mm/yr, p = 0.6465). Over the complete years 2000–2025 the slope is **+0.219 mm/yr** (p = 0.96); eThekwini +6.6 vs +3.2 stored; Cape Town −2.9 vs −3.9. CV: Alfred Nzo 17.05 % stored, 14.70 % for 2000–2025. ENSO: 2026's Niño 3.4 value is −41.28, which flips the correlation (Alfred Nzo +0.48 stored, −0.43 over 2000–2025; Namakwa +0.03 vs −0.45). | Never served. The climate statistics table recomputes the ENSO correlation over the complete years. **Ask:** compute record statistics on complete years only. |
| `4_6_streamflow_and_climate_linkage_annual _master` · all columns | Already withheld (D5). New evidence: `Streamflow_Anomaly_percent` = the MAP anomaly (r = 0.999), with a mean that includes 2026; `Climate_Streamflow_Elasticity` ≈ 1.05 in most districts (0.31–2.23), i.e. runoff ∝ rainfall, while published SA elasticities are about 2–3.5; `Q90_LowFlow_m3_year` and `Q10_FloodFlow_m3_year` are one value per district repeated every year, on the implausible MAR basis (§4). | Never served. |

### Passed → served (live)

| Table · column | Evidence |
| --- | --- |
| `meteorological_drought_master` · `relative_humidity_pct` | 42.1–97.9 % (nothing outside 0–100), no nulls; district-specific seasonal cycles (Johannesburg 73 % in January, 60 % in August; Cape Town 76–79 % with its maximum in the winter-rain months); daily r = 0.49 with rainfall. |
| `satellite_remote_sensing` · `surface_water_extent_km2` | ≥ 0 everywhere (min 0.0015 km²); district-specific (uMkhanyakude ≈ 1 100 km² with Lake St Lucia, falling to 375 km² in the May 2016 drought; Xhariep ≈ 225 km² with the Gariep Dam; eThekwini ≈ 20 km²). |
| `6.1` · `Hourly_Rainfall_Intensity_mm_h` (and its duplicate `rainfall_intensity_mm_h`) | Not constant (4 305 distinct values, 0–36.8 mm/h) and 0 on dry days. **Note:** it equals the daily total × 0.15–0.20 (r = 1.000), i.e. a proxy derived from daily rainfall, not gauge-measured hourly intensity. |
| `6.1` · `CHIRPS_Daily_Rainfall_Estimate_mm_day` | Plausible, but = `Daily_Rainfall_Total_mm` × 0.95–1.05 (r = 1.000; never > 0 on a dry day): derived from the gauge series, not an independent satellite estimate. **Ask:** confirm the source. |
| `6.1` · `TAMSAT_Satellite_Rainfall_Estimate_mm_dekad` | One value per district-**month** (exactly 1 distinct value per month), ≈ monthly total ÷ 3 (ratio 0.986, r = 0.998): a monthly value expressed per dekad, repeated on every daily row. Served as a **monthly** variable (the spec expects dekadal). |
| `6.1` · `Seasonal_Total_OND/JFM/AMJ/JAS_mm` | One value per district-year; each equals the sum of the daily totals of its three calendar months in the same year (mean absolute difference 0.00 mm over 1 352 district-years). OND 2026 is 0 (not observed yet); partial years are never served. |
| `4_1` · `Heatwave_Frequency_Days`, `Heatwave_Duration_Max_Consecutive_Days`, `LST_Day_C`, `LST_Night_C`, `Frost_Day_Flag` | Frequency = count of days with Tmax > 35 °C in the year (1 404 / 1 404). Duration = longest run of days with Tmax > 35 °C in the **year** (1 404 / 1 404; the registry said monthly, corrected to annual). LST day −7.6 … 56.9 °C, r = 0.81 with Tmax, mean LST day − Tmax = +2.5 °C; LST night − Tmin = +0.2 °C; day < night on 0.3 % of rows. Frost flag = Tmin < 0 °C (checked in issue #1). |
| `4_2` · `R10mm_Days`, `R20mm_Days`, `PRCPTOT_mm`, `Rx1day_mm`, `SDII_mm_per_wet_day`, `CDD_Days`, `CWD_Days` | Recomputed from `6.1` daily rainfall for 1 300 district-years (2001–2025): R10mm, R20mm, PRCPTOT and Rx1day equal in 1 300 / 1 300, SDII within 0.003 mm; CDD equal in 1 269 and CWD in 1 208 (the rest are runs that cross 1 January). |
| `4_3` · `MAP_mm`, `Precip_DJF/MAM/JJA/SON_mm`, `Nino34_Annual_Mean` | MAP and the seasonal totals equal the sums of `6.1` daily rainfall (0.00 mm). **DJF = January + February + December of the same calendar year**, not the season across the year boundary. Niño 3.4: −0.85 … +1.55 for 2000–2025 (2015 El Niño +1.55); 2026 (−41.28) is a partial year and never served. |
| `5.2` · `Growing_Degree_Days_GDD_C` | = Σ daily max(Tavg − 10 °C, 0) from `4_1` in 624 / 624 district-months of 2015 (0.00 difference): degree-days, unit °C·days (the column suffix says °C). |
| `6.2` · `RAI_Rainfall_Anomaly_Index` | −3.42 … +4.00 (van Rooy scale), r = 0.96 with SPI-1 and 0.89 with the monthly total. |
| `runoff_variables_master` · `Soil_Moisture_Prior_m3m3` | 0.025–0.475 m³/m³, 4 352 distinct values. |

Raw columns kept out of the database view although the API uses them: `Daily_Surface_Runoff_Volume_m3_day` (volume
on the constant 6 500 km² area; the runoff page serves depth × real district area) and `Baseflow_Component_m3s` (same
scaling; only the baseflow index, a ratio, is served).

## 16. New tables not registered yet (P2) — found 2026-10-15

The database now holds **27** tables. 13 appeared after the Phase 0 inventory (2026-10-14):
`8_1_reservoir_operational_master`, `9_1_hydrological_station_master`, `9_2_catchment_master`,
`9_3_sansa_earth_observation_master`, `10_1_borehole_and_aquifer_master` (long format: `variable_name` / `value` /
`unit`), `10_2_recharge_supporting _master`, `11_1_hydrogeological`, `11.2_recharge_and_discharge`,
`11.3_aquifer_sustainability` and the four `12.x` water-quality tables. They cover spec §8–§12, for which the backend
has no module yet. They are **not served**: each needs a page, its keys and time columns registered, and a
plausibility check. First observations: `8_1` and `10_1` hold rows dated up to **2026-12-31**, after the newest record
in every other table (2026-09-06), and future-dated rows cannot be observations; `10_1` units are stored with broken
encoding (`m�/day`); `10_1` has two variables called hydraulic head with different units (`hydraulic_head_amsl_m` in
m amsl, `hydraulic_head_m_amsl` in m bgl). The catalogue generator lists them (`app/registry/catalogue.yaml` →
`unregistered`, and `docs/CATEGORY_CATALOGUE.md`) so nothing in the database is hidden.

## 17. Widespread rain on the newest day (P3) — 2026-10-15

The rainfall page's "Consecutive Dry Days (current)" KPI is 1 day nationally in early September, in the dry season
for most of the country. The calculation is correct (days since the last day with ≥ 1 mm, per district, then the
area-weighted mean; tested against SQL in `tests/test_rainfall.py`). The data explain it: on **2026-09-06**, the
newest day in the database, **42 of 52 districts** have ≥ 1 mm and the other 10 (Western Cape, Karoo, Namakwa,
Z F Mgcawu, Capricorn) had rain 1–3 days earlier. In July–August 2000–2025 Johannesburg has ≥ 1 mm on 4.3 % of days
and Waterberg on 2.0 %, yet between 1 July and 6 September 2026 both have 4–5 wet days (Johannesburg 48 mm).
**Ask:** confirm that early September 2026 really had a country-wide rain event, or check the last load (e.g. a
forecast or a partial day loaded as observations).

