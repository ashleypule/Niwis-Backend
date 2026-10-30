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

## Drought

| Page | Panel | Value shown (national, default period) | Recomputed in SQL | Match? | Method | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| Drought | KPI Drought Class | No drought; 8 % of area in D1–D4 (SPI-6, Aug 2026) | classes by area: None 87.79 %, D0 3.74 %, D1 8.47 %; no class reaches 20 % cumulatively → None | ✅ | district SPI-6 rounded to 2 dp → spec §3.4 class; worst class covering ≥ 20 % of the area | — |
| Drought | KPI SPI-3 / SPI-6 / SPEI-12 | −0.83 (D1) / 0.44 / 1.07, Aug 2026 | equal | ✅ | area-weighted (labelled "area-weighted mean") | — |
| Drought | KPI VCI | 87.4 %, Dec 2025, stale | 87.4 | ✅ | `satellite_remote_sensing.vci` Dec 2025, area-weighted | — |
| Drought | KPI Soil Moisture Anomaly | +12.9 %, Dec 2025 vs 2015–2025 | 12.9 (0.1216 vs 0.1077) | ✅ | national surface soil moisture vs the Dec mean of 2015–2025; the constant fill before Mar 2015 (52 distinct values in 15 years) is excluded | — |
| Drought | KPI SSI-3 (modelled runoff) | 0.49, Aug 2026, gamma fit 2000–2025 | 0.49 with a scipy MLE gamma fit and 0.49 with Thom's approximate fit, on the SQL monthly depth series (320 months) | ✅ | 3-month runoff-depth totals, gamma per calendar month, zeros by mixing; `scripts/audit_step1/ssi.py` | — |
| Drought | KPI Dam Storage | null (withheld, D2) | `hydrological_drought_master` national storage: 1 distinct value since Dec 2021 (VERIFICATION) | ✅ | correctly withheld | — |
| Drought | KPI SGI | null (latest valid Aug 2024, stale) | SGI Aug 2024 = 0.31; Sep 2024 – Dec 2025 repeat 0.37 | ✅ | correctly withheld (frozen series) | — |
| Drought | KPI Settlements at Risk | null (no register) | n/a | ✅ | | — |
| Drought | Chart SPI (scale 1 / 3 / 6 / 12 / 24) | Aug 2026: −0.70 / −0.83 / 0.44 / 1.20 / 1.15 | equal | ✅ | area-weighted, complete months | — |
| Drought | Chart Rainfall & SPEI-3 | rain = Monthly chart; SPEI-3 Aug 2026 −0.05 | equal | ✅ | | — |
| Drought | Chart Rainfall anomaly (windows) | identical to the Rainfall page's anomaly series | equal | ✅ | one implementation | — |
| Drought | Chart Dam storage & flows | 501 (withheld) | | ✅ | | — |
| Drought | Chart Groundwater (SGI) | Sep 2023 1.22 … Aug 2024 0.31; level series withheld | equal | ✅ | own 12-month window ending at the last valid month | — |
| Drought | Chart Soil moisture | Dec 2025 surface 0.122, root-zone 0.154; Dec range 0.077–0.153, mean 0.108; Sep 0.076–0.107 / 0.094 | equal | ✅ | national series per calendar month over 2015–2025 | — |
| Drought | Chart Vegetation | Dec 2025 NDVI 0.433, VCI 87.4, VHI 93.3 | equal | ✅ | | — |
| Drought | Chart Area in drought (SPI-1 / 3 / 6 / 12 / 24) | e.g. SPI-6 Sep 2025 D0 12.9 / D1 40.3 / D2 0.2; Aug 2026 D0 3.7 / D1 8.5 | all 10 month × scale rows equal | ✅ | Σ district area per class / total area | — |
| Drought | Breakdown Severity (km²) | None 1 073 153, D0 45 775, D1 103 508, D2–D4 0; 8 % in drought | equal | ✅ | | — |
| Drought | Breakdown Province area in D1–D4 | EC 13.5, KZN 85.3, others 0 | equal | ✅ | | — |
| Drought | Table Affected provinces | KZN −0.93 D1 85 %; MP −0.18 D0 0 %; GP 0.06 None … WC 1.45 | SPI-6 means equal; area-rule classes equal (KZN rank 2, MP rank 1, rest 0) | ✅ | class by the area rule, not the class of the mean | — |
| Drought | Table Municipalities | iLembe −1.24, O.R. Tambo −1.18, eThekwini −1.17, Alfred Nzo −1.15, Ugu −1.08, Harry Gwala −1.07 | equal | ✅ | | — |
| Drought | Table Cascade — 1 precipitation | SPI-6 0.44, No drought, severity 8 % | equal | ✅ | as the KPI | — |
| Drought | Table Cascade — 2 soil moisture | +13 % (12.9) vs 2015–2025, no class | equal | ✅ | as the KPI | — |
| Drought | Table Cascade — 3 vegetation | VHI 93 % (93.3), Excellent, severity 0 % | 93.3; area with VHI < 40 = 0 % | ✅ | | — |
| Drought | Table Cascade — 4 streamflow | SSI-3 0.66, No drought, severity 16 %, Aug 2026 | 0.66 (52 district SSI-3 of `9_1` monthly mean flow, area-weighted); 16 % of area at D1 or worse | ✅ | gamma fit per district 2000–2025 (`ssi.py`) | — |
| Drought | Table Cascade — 5 reservoir | null (withheld) | | ✅ | | — |
| Drought | Table Cascade — 6 groundwater | +0.23 m vs September mean 2000–2025, Sep 2026, no class | +0.233 m (52 districts, 100 % coverage) | ✅ | monthly mean of `10_1` depth samples; anomaly = Sep mean of 2000–2025 − Sep 2026 (+ = shallower) | — |
| Drought | Table Cascade — 7 socio-economic | null | | ✅ | | — |
| Drought | Table Sources | statuses Online / Delayed from table dates | equal to `max(date)` | ✅ | | — |
| Drought | Matrix VCI by province (Jan–Dec 2025) | e.g. EC Jan 67.7, Dec 75.6; NC Jan 25.7, Dec 91.1; WC Dec 60.4 | 18 sampled cells equal | ✅ | | — |
| Drought | Maps drought-class / municipal-class / province-class | SPI-6 values; DC44 D1, DC12 None; KZN D1, MP D0 | equal | ✅ | | — |
| Drought | Maps SPI-1 … SPI-24, SPEI-3, SPEI-12 | e.g. DC44 SPI-24 1.05, DC23 SPEI-3 −0.96, DC32 SPEI-12 2.88 | equal; SPI-24 DC44 is stored as 1.055 and prints 1.05 (Python round-half-even) where SQL prints 1.06 | ⚠️ | rounding tie at the 3rd decimal only; no data error | — |
| Drought | Maps NDVI / VCI / soil moisture (Dec 2025) | DC44 0.650 / 99.2 / 0.184 | equal | ✅ | | — |
| Drought | Map rain-anomaly (3 months to 06 Sep 2026) | CPT −66.8, DC1 −73.0, BUF 47.9 | equal | ✅ | 92-day window vs pro-rated normals | — |
| Drought | Forecast tab: SPI-3 / SPI-6 / SPEI-3 / area, indicators, early warning, class probability, recovery, province risk | histories = the dashboard series (SPI-3 −0.83, SPEI-3 −0.05, area 8.5 %); `asOf` 2026-08-01; the derived tables agree with each other (recovery 3 months 91.2 % = 100 − P(D1–D4) 8.9 %) | | ✅ | see "Forecast tabs" | — |
| Drought | Districts CPT / DC34 / DC44 | SPI-3 −0.54 / −0.79 / −0.87; SPI-6 0.56 / 0.24 / −1.15; SPEI-12 −1.18 / 3.00 / −0.24; VCI 42.4 / 75.0 / 99.2; SM anomaly −27.3 / +9.8 / +4.6; SSI-3 −3.60 / 0.98 / 1.04; cascade SSI-3 of flow −1.07 / 3.58 / −0.01; GW anomaly −0.22 / +0.65 / +0.38 | all equal | ✅ | district's own values | — |
