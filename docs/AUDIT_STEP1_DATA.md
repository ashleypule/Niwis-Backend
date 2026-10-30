# System audit — step 1: data correctness (backend)

> Audit of every live panel against the database, 2026-10-30. For each panel the value the API serves for South
> Africa over the default period was recomputed with **independent SQL written for this audit** (not the API's query
> code), run read-only on Supabase; where a value needs a statistical fit that SQL cannot do (gamma-fitted SSI, Sen's
> slope is done in SQL) the SQL-extracted series was refitted in a separate script. Area weights come from
> `reference/geo/districts.json` (the only non-SQL input, as in `scripts/verify.py`). The SQL is committed under
> `scripts/audit_step1/` (one file per page, statements separated by `;;`, `{W}` = the district-name / code / province /
> area table) so it can be re-run; `scripts/verify.py` keeps the regression checks for every fix.
>
> Conventions. "Latest complete month" is the last month fully covered by the module's daily table (D5): for rainfall /
> SPI that is **Aug 2026** (daily data end 2026-09-06), for the satellite table **Dec 2025**, for `9_1` flows **Aug 2026**
> (data to 2026-09-24), for `10_1` telemetry **Sep 2026** (newest non-future sample 2026-10-15). "12 months" for
> daily-derived totals is the 365 days ending on the newest daily record (07 Sep 2025 – 06 Sep 2026), and every such
> panel says so in its sublabel / title; monthly indices use complete months. Three districts were spot-checked on every
> page: City of Cape Town (CPT), Vhembe (DC34), Alfred Nzo (DC44). Classes use `app/core/thresholds.json` (spec §3.4:
> value rounded to 2 dp, then `<=`).
>
> Match column: ✅ equal to the displayed precision · ⚠️ matches with a note · ❌ mismatch (fixed, see "Fix").

## Rainfall

| Page | Panel | Value shown (national, default period) | Recomputed in SQL | Match? | Method | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| Rainfall | KPI Total Rainfall | 701.7 mm (07 Sep 2025 – 06 Sep 2026); delta +113.0 mm vs the year before | 701.7; previous window 588.7 → +113.0 | ✅ | Σ `6.1.Daily_Rainfall_Total_mm` per district over the 365-day window (365 / 365 days present in all 52), area-weighted mean | — |
| Rainfall | KPI Rainfall Anomaly | +31.8 % vs 2000–2026 normal (533 mm) | 31.8 (normal 533) | ✅ | normal = `Monthly_Total_Precipitation_mm − Monthly_Anomaly_mm` (one value per district-month, verified constant), pro-rated by the days of each month in the window; (ΣwP − ΣwN) / ΣwN | — |
| Rainfall | KPI Heavy Rain Days (R20mm) | 5.7 days | 5.7 | ✅ | days ≥ 20 mm per district in the window (count = sum over time), area-weighted district mean | — |
| Rainfall | KPI Max Daily Rainfall | 157.8 mm, Vhembe, 12 Jan 2026 | 157.84, Vhembe, 2026-01-12 | ✅ | max over districts and days | — |
| Rainfall | KPI Consecutive Dry Days | 1 day (to 06 Sep) | 0.66 → 1 | ✅ | days since the last day ≥ 1 mm per district, area-weighted (CPT 3, DC10 2, DC44 0 as the map) | — |
| Rainfall | KPI SPI-6 | 0.44, Aug 2026, No drought | 0.44 | ✅ | `6.2.SPI_6` Aug 2026, area-weighted; class None (> −0.5) | — |
| Rainfall | KPI SPEI-6 | 0.36, Aug 2026 | 0.36 | ✅ | `meteorological_drought_master.spei_6` (one value per month, verified: 1 distinct value in Aug), area-weighted | — |
| Rainfall | KPI Active Rainfall Stations | null (withheld: no station data) | n/a | ✅ | correctly withheld | — |
| Rainfall | Chart Monthly rainfall (2026 / 2025 / 2024 / normal) | e.g. 2026: Jan 82.3 … Aug 17.2, Sep 23.9 (to 06 Sep, labelled); normal Jan 84.0 … Dec 76.8 | all 45 values equal | ✅ | monthly Σ daily per district → area-weighted; normal as above per calendar month | — |
| Rainfall | Chart Anomaly (window 1 / 3 / 12) | 12 bars each, e.g. Aug 2026 +3.6 / +7.1 / +27.5 | all 36 values equal | ✅ | rolling n-month total vs Σ of the same months' normals, complete months only | — |
| Rainfall | Chart SPI / SPEI | Aug 2026: SPI-1 −0.70, SPI-3 −0.83, SPI-6 0.44, SPI-12 1.20, SPEI-6 0.36, SPEI-12 1.07 | equal | ✅ | area-weighted district indices, complete months | — |
| Rainfall | Chart Intensity | Aug 2026: daily max 35.2 mm, R20 0.14 days | 35.16 / 0.14 | ✅ | max over districts; R20 area-weighted | — |
| Rainfall | Chart Dry / wet spells | 2025: CDD 41.6, CWD 9.4; 2000: 49.9 / 9.6 | equal | ✅ | `4_2` annual indices, area-weighted, full years 2000–2025 | — |
| Rainfall | Chart Station (DC44) | Aug 2026: rain 48.0, SPI-3 −0.87, SPI-12 −0.29 | equal | ✅ | the district's own values | — |
| Rainfall | Breakdown Seasonal | 2025: JFM 270.7, AMJ 125.6, JAS 54.3, OND 202.7 | equal; stored seasonal columns = Σ daily of those months (max |diff| 0.00) | ✅ | `6.1` seasonal columns (one per district-year), area-weighted | — |
| Rainfall | Breakdown Events (count / max) | 2017–2026: 16, 11, 10, 8, 14, 16, 15, 6, 28, 24 events; max 220.8 mm (2022) | equal | ✅ | district-days > 50 mm per year summed over the scope; max daily per year | — |
| Rainfall | Table Provinces | MP 1174.5 mm +41.6 %, GP 1052.7 +42.5 … NC 375.0 +38.7; status None for all | all 9 totals and anomalies equal; SPI-12 Aug 2026 0.31–2.61 → None | ✅ | per-province area-weighted totals / normals; status = class of area-weighted SPI-12 | — |
| Rainfall | Table Extremes (wettest / driest) | Mopani 1254.6 … / Namakwa 178.0 … | equal | ✅ | district window totals ranked | — |
| Rainfall | Table Statistics | annual 653 / clim 528 / trend −21.56; rainy days 99 / 88 / −6.42; R20 5.0 / 3.1 / 0.34; Rx1day 35.1 / 30.4 / 0.09; CDD 42 / 55 / 3.78 | all equal (Sen's slope = median of pairwise slopes × 10 in SQL) | ✅ | 2025 vs the 2000–2025 mean of area-weighted annual values (`4_3.MAP_mm`, `4_2`, daily ≥ 1 mm) | — |
| Rainfall | Table Sources | table metadata (newest dates 2026-09-06 / 2026-09-01 / 2026) | equal to `max(date)` per table | ✅ | registry + table statistics | — |
| Rainfall | Maps total / municipal-total / anomaly | DC44 964.7 mm / −1.9 %; CPT 458.6 / −21.7; DC34 1119.1 / +107.5; DC6 178.0 / +5.6 | equal | ✅ | per-district window total / anomaly | — |
| Rainfall | Map monthly (Sep 2026, to 06 Sep) | DC42 17.3, CPT 1.1, DC34 9.7, DC44 31.9 | equal | ✅ | Σ daily 1–6 Sep, labelled partial | — |
| Rainfall | Map MAP 2000–2025 | BUF 818, CPT 589, DC34 520, DC44 986 | equal (26 years each) | ✅ | mean of `4_3.MAP_mm` | — |
| Rainfall | Maps SPI-1 / 3 / 6 / 12, SPEI-6 (Aug 2026) | e.g. DC44 −0.59 / −0.87 / −1.15 / −0.29 / −1.34; DC23 SPEI-6 −1.15 | equal | ✅ | stored values | — |
| Rainfall | Map CDD / R20 (2025) | CDD as KPI note; R20 DC25 16, CPT 5, DC34 11, DC44 9 | equal | ✅ | | — |
| Rainfall | Entity card DC44 | 965 mm, −2 %, SPI-3/6/12 −0.87 / −1.15 / −0.29, area 10 742 km² | equal | ✅ | | — |
| Rainfall | Forecast tab: rainfall / SPI-1 / SPI-3 | history (36 months) = the Monthly chart; `asOf` 2026-08-01 = last complete month; climatology, MASE 0.88 / 0.77 | obs series identical to the dashboard series | ✅ | see "Forecast tabs" | — |
| Rainfall | Districts CPT / DC34 / DC44 (all KPIs) | totals 458.6 / 1119.1 / 964.7; anomalies −21.7 / +107.5 / −1.9; R20 6 / 14 / 4; max 64.4 / 157.8 / 30.7; CDD 3 / 0 / 0; SPI-6 0.56 / 0.24 / −1.15; SPEI-6 0.25 / 0.20 / −1.34 | all equal | ✅ | the district's own values, not the scope aggregate | — |

