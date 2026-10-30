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

## Climate

| Page | Panel | Value shown (national, default period) | Recomputed in SQL | Match? | Method | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| Climate | KPI Temperature Anomaly | +0.21 °C, 12 months to Aug 2026, vs 2000–2026 | 0.211 (stored `4_1.Temperature_Anomaly_C`, one value per month); against a 2000–2025 full-year baseline it would be 0.214 | ⚠️ | mean of the stored monthly anomaly per district, area-weighted; the stored baseline includes the partial 2026 (DATA_GAPS §14) but is labelled, and the difference is below the displayed 0.1 °C | — |
| Climate | KPI Warming Trend | +0.30 °C/decade, Sen's slope 2000–2025, p = 0.01 | 0.303 (median of pairwise slopes of the area-weighted annual mean Tavg) | ✅ | annual mean = Σ daily / days per district-year | — |
| Climate | KPI Rainfall Change | +23.7 % (2025 vs 2000–2025 mean 528 mm) | 23.7 (528) | ✅ | `4_3.MAP_mm`, area-weighted | — |
| Climate | KPI Reference ET₀ (annual) | 1 464 mm (2025), −23 mm vs 2000–2025 | 1 463.7; mean 1 486.3 → −23 | ❌ label | Σ daily `et0_mm_day` per district-year, area-weighted; **the table has no 31 December** (364 days in 2025; 365 in 2000 only because it is a leap year), so "2025" was not a full-calendar-year total | sublabel now reads "2025 (364 days: 31 Dec is not in the table)"; test `test_climate_et0_kpi_says_the_year_lacks_31_december` |
| Climate | KPI Mean Annual Runoff | +29.7 % (2025 vs 2000–2025 mean 100 mm) | 130.0 vs 100.3 → 29.7 | ✅ | Σ daily runoff depth (volume ÷ 6 500 km²) per district-year, area-weighted | — |
| Climate | KPI Hot Days (> 35 °C) | 12.9 days (2025), +0.8 vs 2000–2025 | 12.9; mean 12.14 → +0.76; stored `Heatwave_Frequency_Days` = count(Tmax > 35 °C) in all 1 352 district-years | ✅ | one value per district-year, area-weighted | — |
| Climate | KPI Heavy Rain Days / Consecutive Dry Days | 5.0 / 41.6 days (2025) | equal (Rainfall page checks) | ✅ | `4_2`, area-weighted | — |
| Climate | Chart Temperature (long) | Aug 2026: anomaly 1.10, Tmax 22.0, Tavg 14.8, Tmin 7.6; Jan 2000: −2.57 / 26.1 / 20.7 / 15.3 | equal | ✅ | monthly means of daily values; the stored anomaly equals Tavg − the 2000–2026 same-month mean exactly (CPT, 321 months, max diff 0.000) | — |
| Climate | Chart National trend (temperature / rainfall / ET₀ / runoff) | 2000 → 2025: 17.21 → 18.23 °C; 729.6 → 653.3 mm; 1 421.4 → 1 463.7 mm; 149.0 → 130.0 mm; slopes +0.30 / −21.6 / +16.9 / −3.4 per decade | all equal | ✅ | Sen's slope in SQL | — |
| Climate | Charts Extremes / Extreme rain / Heat days (hot, cold) | 2025: CDD 41.6, CWD 9.4, Rx1day 35.1, Rx5day 74.0; R20 5.0; hot days 12.9; frost nights 4.3 (2024 4.7, 2000 5.2); slopes +3.5 / +0.3 / +0.1 / −0.7 | all equal | ✅ | `4_2` annual indices; frost = Σ `Frost_Day_Flag` per district-year | — |
| Climate | Chart Evaporation | 2025: ET₀ 1 464, PET 1 493, AET 517; 2000: 1 421 / 1 450 / 559 | equal | ✅ | annual sums (ET₀ / PET over 364 days, said on the chart) | — |
| Climate | Chart Runoff and climate | 2025 runoff 130.0 mm, anomaly +29.7 %; 2000 149.0 / +48.7 % | equal | ✅ | | — |
| Climate | Chart Drought frequency | 2025: D2 3.6 %, D3 0.4 %, D4 0, 27 district-months; 2000: 1.8 / 1.6 / 0, 29; **2020: 4.2 / 1.8 / 0.2, 52** | 2025 and 2000 equal; **2020: 4.3 / 1.8 / 0.2, 53** | ❌ | per month: district SPI-3 → spec class, Σ area per class ÷ total, mean of the 12 months. The one-district-month difference is Amajuba, Aug 2020, SPI-3 stored as **−1.295**: the spec rounds to 2 dp and then compares (−1.30 ≤ −1.3 → D2), but `drought_class` rounded the binary float (−1.29499999…) to −1.29 → D1, as JavaScript's `Math.round` does. 127 stored SPI values and 38 SPEI values sit on such ties (−0.495, −0.795, −1.295, −1.595, −1.995); the newest one served is CPT SPI-6 Mar 2026 (−1.595) | `app/core/thresholds.py`: classes now round the value's decimal representation half away from zero (−1.295 → −1.30 → D2); `app/core/stats.py` `rnd` rounds the same way so a displayed −1.30 never sits next to a D1 pill; tests `tests/test_audit_step1.py`, `tests/test_thresholds.py` (tie cases); `verify.py` check "district-months in D2 or worse in 2020 = 53". **Frontend follow-up:** `droughtClassFromSpi` in `src/config/thresholds.ts` uses `Math.round(spi * 100) / 100` and needs the same decimal rounding |
| Climate | Chart Indicator trends (vs 2020) | 2025: temp +0.09 °C, rain +25.0 %, hot −8.3 %, drought −35.9 %, R20 +51.7 % | temp 18.23 − 18.14 ✓; rain ✓; hot ✓; R20 51.7 ✓; drought: (4.0 − 6.3) / 6.3 = −36.5 with the tie fixed | ⚠️ | the same tie as above; the served value is now −36.5 % | fixed by the same change |
| Climate | Breakdown Seasonal change (temperature / rainfall) | temperature 2001–2020: 22.95 / 18.23 / 12.32 / 19.02; 2021–2026: 22.98 / 18.19 / 12.57 / 19.25; rainfall 2001–2020: 223 / 119 / 48 / 118; 2021–2025: 259 / 138 / 45 / 128; 1981–2000 null with the reason | all equal | ✅ | national monthly-mean Tavg by season and period; `4_3` seasonal totals | — |
| Climate | Breakdown Map seasonality | 2000–2009: 552 / 229 / 131 / 52 / 140; 2010–2019: 484 / 222 / 117 / 44 / 100; 2020–2025: 563 / 259 / 132 / 42 / 129 | equal | ✅ | decade means of area-weighted `4_3` columns | — |
| Climate | Table Statistics | 18.1 °C; +0.30 °C/decade (p 0.011); +0.21 °C; 528 mm; −21.6 mm/decade; +3.5 days/decade | equal (18.08 → 18.1) | ✅ | | — |
| Climate | Table Provinces | EC temp +0.37, rain +5.7 %, trend 0.21, hot days 4.9; NC +0.55 / +11.4 / 0.37 / 33.1; WC +0.52 / −26.7 / 0.15 / 10.0; GP −0.38 / +71.4 / 0.44 / 0.0 | all equal (a first SQL attempt double-counted alias spellings and was corrected) | ✅ | province area-weighted anomaly, 2025 vs the mean, Sen's slope, hot days | — |
| Climate | Table Signal chain | +0.21 °C; ET₀ −2 %; rainfall +24 %; runoff +30 %; severity null (no thresholds) | consistent with the KPIs | ✅ | | — |
| Climate | Table Forecast summary (trend extrapolation) | temperature baseline 18.1 → 18.6 / 19.2 / 19.8; hot days 12 → 17 / 24 / 31; rainfall 528 → 479 / 436 / 392; ET₀ 1 486 → 1 519 / 1 553 / 1 586; runoff 100 → 92 / 86 / 79 | temperature 18.61 / 19.21 / 19.82 with intercept = median(x) − slope × 12.5 (pymannkendall); the rest follow from the slopes above | ✅ | labelled a Sen's-slope extrapolation, not a projection | — |
| Climate | Table Risk outlook 2050 | heat stress Very High (z 2.33), hot days High (1.95), rainfall Low (p 0.31), ET₀ Low (p 0.08), runoff Low (p 0.43) | z = change ÷ s.d. of the record: (19.21 − 18.08) / 0.487 = 2.32; (24 − 12.14) / 5.97 = 1.99; ET₀ (1 553 − 1 486) / 34.6 = 1.93; runoff 14 / 21.7 = 0.66 | ✅ | | — |
| Climate | Maps temp-trend / temp-anomaly / mean-temp | CPT 0.109 / 0.41 / 16.7; DC34 0.221 / −0.48 / 21.7; DC44 0.284 / 0.48 / 15.1 | equal | ✅ | | — |
| Climate | Maps rain-trend / hot-days / cdd / et0 | rain-trend 2016–2025 vs 2000–2015: BUF −10.7, CPT −11.6, DC34 3.4, DC44 4.1; hot days DC45 21, DC5 20; CDD CPT 40, DC34 32, DC44 22; ET₀ mean CPT 1 245, DC34 1 548, DC44 1 350 | equal | ✅ | | — |
| Climate | Forecast tab: temperature anomaly / hot days | histories = the Temperature chart's anomaly (Aug 2026 1.10) and the area-weighted district-month count of Tmax > 35 °C (Jan 2026 4.9, Dec 2025 2.7); `asOf` 2026-08-01; SARIMA | hot-day months recomputed: 4.91 / 2.73 / 0.89 / 1.22 | ✅ | see "Forecast tabs" | — |
| Climate | Districts CPT / DC34 / DC44 | anomaly 0.41 / −0.48 / 0.48; trend 0.11 / 0.22 / 0.28; rain change −22.7 / +51.3 / +19.9; ET₀ 1 257 / 1 506 / 1 317 (delta +12 / −42 / −32); runoff −22.6 / +64.8 / +23.8; hot days 0 / 5 / 0; mean temp 16.7 / 21.7 / 15.1 | all equal | ✅ | | — |

## Vegetation

| Page | Panel | Value shown (national, default period) | Recomputed in SQL | Match? | Method | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| Vegetation | KPIs NDVI / VCI / VHI / TCI / LST / Soil moisture | 0.433 / 87.4 % / 93.3 % / 99.2 % / 31.3 °C / 0.122 m³/m³, Dec 2025 (stale) | equal (52 districts) | ✅ | `satellite_remote_sensing` Dec 2025, area-weighted; classes Excellent (≥ 80) | — |
| Vegetation | KPI Area Under Stress | 0.0 % (VHI < 40 %) | 0.0 | ✅ | Σ area with VHI < 40 ÷ total | — |
| Vegetation | KPI Vegetation Anomaly | +19.2 % (NDVI vs Dec 2001–2025 mean) | 19.2 | ✅ | national NDVI vs the Dec mean of the national series 2001–2025 | — |
| Vegetation | Chart Indices (every `set`) | NDVI 0.433, VCI 87.4, VHI 93.3, TCI 99.2, NDWI −0.481, LST 31.3, SM 0.122 (Dec 2025); EVI / SAVI / MSAVI2 / VPI / ASI / SVI / NDII null with the reason | equal | ✅ | withheld indices exist only in the failed `5.1` table | — |
| Vegetation | Chart Index vs normal (NDVI / VCI / VHI / TCI / NDWI / LST / SM) | NDVI Dec 2025 0.433, band 0.327–0.400 (mean ± 1 s.d. of the national Dec series 2001–2025), previous year 0.352; VCI band 29.1–71.1, previous 44.9 | equal | ✅ | | — |
| Vegetation | Chart NDVI anomaly | Sep–Dec 2025: +16.7, +15.6, +19.2, +19.2 % | equal | ✅ | national series vs its same-month 2001–2025 mean | — |
| Vegetation | Chart Drivers (rain / temp / pet / sm) | Aug 2026: rainfall 17.2 mm (= Monthly chart), Tmax 22.0 °C, ET₀ 3.26 mm/day; soil moisture Dec 2025 0.122 | equal | ✅ | monthly means of daily values; ET₀ as mm/day because December lacks the 31st | — |
| Vegetation | Breakdown Classification (VHI classes) | Excellent 85.2, Good 13.7, Fair 1.2, Poor 0, Very Poor 0 % | equal | ✅ | Σ area per class | — |
| Vegetation | Breakdown By province | EC Excellent 50.3 / Good 48.6 / Fair 1.2; LP 84.1 / 15.9; WC 40.6 / 49.9 / 9.5; others 100 % Excellent | equal | ✅ | | — |
| Vegetation | Breakdown Seasonal mean NDVI | 2025: DJF 0.407, MAM 0.473, JJA 0.343, SON 0.354; DJF 2019 0.343 | equal | ✅ | DJF of year Y = Dec Y−1 + Jan, Feb Y of the national series | — |
| Vegetation | Breakdown Drivers (change vs normal, last 6 months) | rainfall +65.2 % (Mar–Aug 2026), mean temperature +0.12 °C, ET₀ −2.4 %, GDD −1.0 °C·days, LST −5.94 °C (Jul–Dec 2025) | equal | ✅ | each driver's own last 6 complete months vs the same months' 2001–2025 mean; temperatures in °C, amounts in % | — |
| Vegetation | Table Provinces | FS NDVI 0.520, VCI 100, +33.1 %; NW 0.551 / 100 / +44.5; EC 0.472 / 75.6 / +8.3; WC 0.269 / 60.4 / +1.2 | equal | ✅ | province series vs its Dec 2001–2025 mean | — |
| Vegetation | Table Hotspots | Overberg VCI 20.7 (Poor), NDVI change −0.019; CPT 42.4 / −0.009; NMA 42.6 / −0.032; Mopani 48.0 / +0.003 | equal | ✅ | lowest VCI; change = NDVI − the district's Dec 2001–2025 mean | — |
| Vegetation | Maps ndvi / vci / vhi / tci / ndwi / lst / sm | CPT 0.373 / 42.4 / 71.2 / 100 / −0.438 / 31.5 / 0.067; DC34 0.540 / 75.0 / 84.6 / 94.2 / −0.490 / 30.9 / 0.124; DC44 0.650 / 99.2 / 99.6 / 100 / −0.571 / 26.5 / 0.184 | equal | ✅ | stored values | — |
| Vegetation | Map NDVI anomaly | CPT −0.009, DC34 0.054, DC44 0.078, DC6 0.006 | equal | ✅ | NDVI − the district's Dec 2001–2025 mean | — |
| Vegetation | Map NDVI trend | CPT −0.0121, DC34 0.0195, DC44 0.0186 per decade; legend breaks ±0.013 / ±0.019 | equal (Sen's slope of annual mean NDVI 2001–2025 in SQL) | ✅ | | — |
| Vegetation | Forecast tab: NDVI / VCI, indicators, recovery probability | histories = the dashboard series (Dec 2025 0.433 / 87.4); `asOf` 2025-12-01; seasonal naive (NDVI) / ETS (VCI) | | ✅ | see "Forecast tabs" | — |
| Vegetation | Districts CPT / DC34 / DC44 | NDVI 0.373 / 0.540 / 0.650; VCI 42.4 / 75.0 / 99.2; VHI 71.2 / 84.6 / 99.6; TCI 100 / 94.2 / 100; LST 31.5 / 30.9 / 26.5; SM 0.067 / 0.124 / 0.184; anomaly −2.3 / +11.2 / +13.7 % | all equal | ✅ | | — |

## Runoff

| Page | Panel | Value shown (national, default period) | Recomputed in SQL | Match? | Method | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| Runoff | KPI Annual Runoff | 139.0 mm (07 Sep 2025 – 06 Sep 2026) | 139.0 | ✅ | Σ daily depth (model volume ÷ 6 500 km²) per district, area-weighted | — |
| Runoff | KPI Runoff Anomaly | +37.3 % vs the 2000–2026 same-month mean stored in 7.2 | 37.3 (normal 101.2 mm) | ✅ | normal per district-month = depth ÷ (1 + stored anomaly / 100), median over years, pro-rated by days | — |
| Runoff | KPI Peak Flow | null (withheld: the table's peaks are scaled to 6 500 km²) | | ✅ | see "Derivable in step 3" | — |
| Runoff | KPI Baseflow Index | 0.32 | mean of district ratios 0.320; ratio of the area-weighted sums 0.320 | ✅ | now Σ baseflow / Σ flow of the scope (identical here because the model's BFI is nearly constant) | label and method aligned with the coefficient (same change) |
| Runoff | KPI Runoff Coefficient | **0.186** labelled "Σ runoff / Σ rainfall, 12 months" | **0.198** = Σ runoff / Σ rainfall of the area-weighted totals; 0.186 is the area-weighted mean of the 52 district ratios | ❌ | the label and the statistics table (0.198) say ratio of sums; the KPI averaged district ratios | KPI = ratio of the scope's totals (`runoff/service.py`); test `test_runoff_coefficient_kpi_is_the_ratio_of_the_window_sums`; verify.py check (national 0.198) |
| Runoff | KPI SSI-6 (modelled runoff) | 2.47, Aug 2026 | 2.47 (scipy MLE and Thom fits); SSI-1 0.39, SSI-3 0.49, SSI-12 1.87 | ✅ | gamma per calendar month 2000–2025 on the national depth series | — |
| Runoff | KPIs Stations normal / low flow | null (no stations) | | ✅ | | — |
| Runoff | Chart Discharge (derived) — daily | 07 Sep 2025 450.01 m³/s … 06 Sep 2026 18 311.16 | equal (Σ district depth × area × 1000 / 86 400, 52 districts every day) | ✅ | | — |
| Runoff | Chart Discharge — monthly mean / normal | **Sep 2025 2 823.63**, Sep 2026 9 983.48; normal Sep 1 864.98, Jan 7 559.20 | Sep 2025 whole month **2 342.51**; 2 823.63 is the mean of 7–30 Sep and 9 983.48 of 1–6 Sep (the window's partial edge months); normals equal | ❌ | a six-day "monthly mean" plotted against a full-month normal | monthly means now cover complete months only (Sep 2025 – Aug 2026); the daily line keeps the window; same fix on the Surface Water page; tests `test_runoff_discharge_monthly_means_cover_complete_months_only`, `test_surface_water_…`; verify.py checks |
| Runoff | Chart Runoff anomaly | Sep 2025 +25.4 … Aug 2026 +11.6 %; 12-month mean 37.6 | all 12 equal | ✅ | area-weighted depth vs area-weighted 7.2 normal | — |
| Runoff | Chart SSI (1 / 3 / 6 / 12) | Aug 2026 0.39 / 0.49 / 2.47 / 1.87 | equal | ✅ | | — |
| Runoff | Charts Rainfall & runoff / Water balance | Aug 2026 rain 17.2, runoff 3.2, AET 22.6; Sep 2025 29.1 | equal | ✅ | | — |
| Runoff | Chart Flood frequency (LP3) | fit 2 / 5 / 10 / 20 / 50 / 100 yr: 38 305 / 45 397 / 49 397 / 52 848 / 56 887 / 59 668 m³/s; largest maximum 64 576 (2010) | annual maxima of the national daily flow 2000–2025 equal (26 values); LP3 refit (log10, Pearson III, unbiased skew) 38 305.1 / 45 397.0 / 49 397.3 / 52 848.2 / 56 887.0 / 59 667.6 | ✅ | `scripts/audit_step1/ssi.py` session | — |
| Runoff | Chart Flow-duration curves | last 12 months max 57 525, median 2 601.5; long-term max 64 576, median 1 434.3 | equal | ✅ | | — |
| Runoff | Chart Average flow by month / Runoff volume by month | Jan 2026 8 123.15 m³/s, lt Jan 7 559.20; Aug 2026 1 475.76; volumes Jan 2026 21 757.05 Mm³, lt Jan 20 246.55, Aug 3 952.67, Dec 2025 20 464.76 | equal | ✅ | complete months only | — |
| Runoff | Breakdown Runoff volume by WMA | Limpopo 19 696 / 11 935 … Berg-Olifants 3 001 / 3 249 Mm³ | all 9 × 2 equal | ✅ | district depth × overlap area | — |
| Runoff | Table Statistics | runoff 139.0 / 100.3 / +38.7 / −3.39; coefficient 0.198 / 0.189 / +0.009 / 0.0019 | equal | ✅ | annual ratios of area-weighted totals | — |
| Runoff | Table High-flow days | 2 431 events; Pixley ka Seme 23 Feb 2026 13 433.5 m³/s, Sarah Baartman 11 485.5, Vhembe 11 058.4, ZF Mgcawu 10 751.3 … | equal (district Q10 = 90th percentile of daily depth 2000–2025) | ✅ | | — |
| Runoff | Table Highest / lowest districts (Aug 2026) | Gert Sibande 97.3 (+75.5); John Taolo Gaetsewe 1.1 (−9.6), CPT 3.0 (−9.9) | equal | ✅ | | — |
| Runoff | Maps annual-runoff / runoff-anomaly / monthly-runoff / runoff-coefficient | DC44 202.0 / −3.5 / 7.0 / 0.213; CPT 85.4 / −20.9 / 0.2 / 0.184; DC34 241.6 / +129.7 / 1.5 / 0.194; BUF coefficient 0.199 | equal | ✅ | | — |
| Runoff | Entity card DC44 | 202 mm, −4 %, 2 170 Mm³, mean flow Aug 2026 41.9 m³/s | 202.0, −3.5, 202.0 × 10 742 / 1000 = 2 169.9, 41.9 | ✅ | | — |
| Runoff | Forecast tab: runoff / flow / flood probability | histories = the monthly depth (Aug 2026 3.23) and the Average-flow chart (1 475.8); Q10 8 355.7 m³/s | equal | ✅ | | — |
| Runoff | Forecast tab: runoff anomaly | **history Aug 2026 42.8 %, Jul −34.7, May 144.8** | the Runoff page's anomaly chart: **11.6 %, −10.9, 169.1**; the forecast target averaged the 52 districts' stored `Runoff_Anomaly_percent` (SQL: 42.8, −34.7, 144.8) — a mean of percentages, not the page's anomaly of the area-weighted depth | ❌ | the forecast tab's history must equal the dashboard series | the `runoff-anomaly` page variable is now a loader over `RunoffService.anomaly_series()` (one method for the chart and the forecast); the next page-forecast round recomputes it; test `test_runoff_anomaly_page_forecast_history_is_the_runoff_pages_series` |
| Runoff | Districts CPT / DC34 / DC44 | depth 85.4 / 241.6 / 202.0; anomaly −20.9 / +129.7 / −3.5; BFI 0.32 ×3; coefficient 0.186 / 0.216 / 0.209; SSI-6 −0.33 / 3.63 / −0.37 | all equal | ✅ | one district: ratio of sums = the district's own ratio | — |

## Dams

| Page | Panel | Value shown (national, default period) | Recomputed in SQL | Match? | Method | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| Dams | KPIs (8), storage / flow / alert charts, breakdowns, tables, point maps | null / 501 with the D2 reason (8_1 storage fails the plausibility check) | 8_1: one row per district-day; the reason text is unchanged since D2 | ✅ | correctly withheld | — |
| Dams | Map Catchment rainfall at the reservoirs (Aug 2026) | BUF 10, CPT 15, DC44 10 mm | Σ daily `rainfall_mm_day` Aug 2026: BUF 10.2, CPT 14.9, DC44 10.4, DC34 2.3 | ✅ | the column is served to 31 Aug 2026 only (`valid_until`): from 1 Sep every district repeats its 31 Aug value (checked: 51 distinct values in Sep and Oct against 502 in Aug) | — |
| Dams | Map Catchment soil moisture (Sep 2026) | BUF 0.274, CPT 0.190, DC44 0.273 | equal (mean of the month's daily values) | ✅ | | — |
| Dams | Entities / entity card DC44 | "Alfred Nzo Reservoir (GDW_794) / (GDW_796)", river "Alfred Nzo Catchment Drainage"; rainfall Aug 2026 10 mm, soil moisture Sep 2026 0.273; storage fields null with the reason | equal | ✅ | | — |
| Dams | Forecast tab | 501 (storage withheld) | | ✅ | | — |

## Surface water

| Page | Panel | Value shown (national, default period) | Recomputed in SQL | Match? | Method | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| Surface water | KPI Mean River Discharge | 422.4 m³/s, Aug 2026 | 422.4 (Σ of the 52 district monthly means of `daily_mean_flow` ÷ 86 400) | ✅ | sum over districts (a flow) | — |
| Surface water | KPI River Water Level | 0.05 m | 0.053 | ✅ | stored monthly stage, area-weighted | — |
| Surface water | KPIs Districts with High / Low Flow | 0 / 0 of 52 | 0 / 0 (district Aug 2026 mean vs its own p90 / p10 of monthly means 2000–2025) | ✅ | | — |
| Surface water | KPI Suspended Sediment | 130 mg/L | 130.2 | ✅ | | — |
| Surface water | KPIs Stations / Flood alerts / E-flow / River WQ | null with reasons | | ✅ | WQ status: see "Derivable in step 3" | — |
| Surface water | Chart River discharge — daily | 25 Sep 2025 312.5 … 24 Sep 2026 585.0 m³/s | equal (52 districts every day) | ✅ | | — |
| Surface water | Chart River discharge — monthly mean / normal | **Sep 2025 991.92**; Oct 444.69 … Aug 2026 422.41; normal Sep 404.48 … Aug 355.63 | Sep 2025 whole month **477.29**; 991.92 is the mean of 25–30 Sep (partial edge); the other months and normals equal | ❌ | as the Runoff page | monthly series = complete months from the window's first month to the latest complete month |
| Surface water | Chart Flow-duration curves | last 12 months max 48 436, median 977.2, min 179.5; long-term max 56 575, median 451.7 | equal | ✅ | | — |
| Surface water | Chart Flow anomaly | Sep 2025 +18.0, Oct −27.0, Jan 2026 +204.8, May +398.7, Aug +18.8 % | equal | ✅ | vs the 2000–2025 same-month mean of the national flow | — |
| Surface water | Chart Average flow by month | Jan 2026 8 950.52, Jan 2025 4 629.74, Dec 2025 2 726.65; lt Jan 2 936.70, Dec 2 102.89, Aug 355.63 | equal | ✅ | | — |
| Surface water | Charts Water level / Sediment (conc, load) | stage Sep 2025 0.053 … Aug 2026 0.053; SSC 181.9 → 130.2; load 91.7 → 65.1 t/day (Jan 2026 3 322.5) | equal | ✅ | load = monthly mean of the daily load, area-weighted | — |
| Surface water | Breakdown Flow status | 0 / 52 / 0 | equal | ✅ | | — |
| Surface water | Table Statistics | discharge 2 531.6 / 1 298.0 / +95 % / +72.34; stage 0.090 / 0.068; velocity 0.047 / 0.040; SSC 139 / 200 / −0.40; load 644 / 300 | equal | ✅ | current = mean of the last 12 monthly values; climatology = all months 2000–2025; Sen's slope of annual means | — |
| Surface water | Table Districts by mean flow | Joe Gqabi 49.3 (−2.2) …; lowest NMA 0.2 (+0.2), ZF Mgcawu 0.2 (−1.4) | equal | ✅ | | — |
| Surface water | Maps discharge / flow-anomaly / flow-status / SSI-3 | CPT 2.8 / −68 % / Normal / −1.07; DC34 17.7 / +952 % / Normal / 3.58; DC44 8.6 / −14 % / Normal / −0.01; BUF 0.5 / −74 % / 0.98 | equal (SSI from the gamma fit per district) | ✅ | | — |
| Surface water | Modelled-runoff maps | the Runoff page's layers (checked there) | | ✅ | | — |
| Surface water | Entity card DC44 | flow 8.6, Normal, SSI-3 −0.01, stage 0.05, velocity 0.07, SSC 78; 9_2: catchment area 10 729 km², tree cover 9.8 %, built-up 0.2 % | equal | ✅ | 9_2 attributes are constant; labelled 2025 (last full year) | — |
| Surface water | Forecast tab: river discharge / runoff / flood probability | history Aug 2026 422.4 = the KPI; Q10 3 312.8 m³/s = 90th percentile of the national monthly flow Jan 2000 – Aug 2026 | equal | ✅ | | — |
| Surface water | Districts CPT / DC34 / DC44 | flow 2.8 / 17.7 / 8.6; stage 0.05 ×3; SSC 45 / 59 / 78; statistics rows (e.g. DC34 discharge 256.1 vs 27.2) | all equal | ✅ | | — |

## Groundwater

| Page | Panel | Value shown (national, default period) | Recomputed in SQL | Match? | Method | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| Groundwater | KPI Mean Groundwater Level | 31.0 m bgl, Sep 2026 | 30.99 | ✅ | `10_1` telemetry depth: monthly mean of samples per district, area-weighted; Sep 2026 is the latest complete sampled month (newest sample 13 Sep 2026) | — |
| Groundwater | KPI Level Anomaly | +0.23 m vs September mean 2000–2025 | 0.233 | ✅ | + = shallower | — |
| Groundwater | KPI SGI | null (valid to Aug 2024) | | ✅ | see "Derivable in step 3" | — |
| Groundwater | KPI Estimated Recharge | 42 mm/yr (11.2 direct recharge 2025) | 41.8 | ✅ | area-weighted | — |
| Groundwater | KPI Annual Abstraction | 1 520.0 Mm³/yr | 1 520.0 | ✅ | sum | — |
| Groundwater | KPI Districts with Rising Levels | 24 of 52 | 24 | ✅ | mean depth Oct 2025 – Sep 2026 < mean Oct 2024 – Sep 2025 | — |
| Groundwater | Chart Depth to water (level / previous year / long-term) | Oct 2025 31.02 … Sep 2026 30.99 (8 sampled months); previous Oct 2024 31.06, Mar 2025 31.02; long-term Oct 31.20 … Sep 31.23 | all equal | ✅ | national series = area-weighted monthly means on months with ≥ 95 % of the area sampled | — |
| Groundwater | Chart Level anomaly | Oct 2025 +0.17 … Sep 2026 +0.23 | equal | ✅ | | — |
| Groundwater | Chart Depth to water — long term | annual means 2000 30.94 … 2025 30.98; long-term 31.33; Sen's slope −0.001 m/decade | equal | ✅ | | — |
| Groundwater | Chart Depth by month (2022–2026) | sampled months only, e.g. Jan 2026 30.83 | equal | ✅ | | — |
| Groundwater | Chart SGI | Sep 2023 1.22 … Aug 2024 0.31 (own window) | equal | ✅ | | — |
| Groundwater | Chart Recharge and abstraction | 2025: 51 088.5 / 1 520.0 / net 49 568.5 Mm³; 2024: 32 190.7 / 1 503.4 | equal | ✅ | direct recharge × district area, abstraction summed | — |
| Groundwater | Breakdown Districts by depth class (Sep 2026) | 0 / 0 / 11 / 14 / 21 / 6 / 0 | equal | ✅ | | — |
| Groundwater | Table Largest declines | West Coast −0.42 (trend +0.007 m/yr), Sekhukhune −0.36 (−0.005), Garden Route −0.34 (−0.009), John Taolo Gaetsewe −0.30, O.R. Tambo −0.28, Ugu −0.28 | equal | ✅ | | — |
| Groundwater | Tables Deepest / Shallowest | Garden Route 58.5 Falling, Dr Ruth Segomotsi Mompati 55.8 Rising, Ngaka Modiri Molema 54.4; CPT 12.9 Rising, West Coast 13.8, Dr Kenneth Kaunda 14.2 | equal | ✅ | | — |
| Groundwater | Maps gw-level / level-change / SGI | BUF 37.4, CPT 12.9, DC34 49.0, DC44 17.7; change DC1 −0.42, DC12 +0.18, DC44 −0.24, CPT 0.00; SGI Aug 2024 DC44 1.01, DC34 −0.69 | equal | ✅ | | — |
| Groundwater | Entity card DC44 | 17.7 m, change −0.24, heads 1 360.2 / 508.2 m amsl, recharge fraction 16.4 %, pumping 31.3 m³/h, abstraction 24 280 m³/yr, direct recharge 79 mm, vadose SM 0.204 | equal | ✅ | | — |
| Groundwater | Forecast tab: depth to groundwater | history = the Depth chart (Sep 2026 30.99) with 121 unsampled months interpolated (said in the note); Random Forest, skill 0.95 | | ✅ | see "Forecast tabs" | — |
| Groundwater | Districts CPT / DC34 / DC44 | level 12.9 / 49.0 / 17.7; anomaly −0.22 / +0.65 / +0.38; recharge 22 / 22 / 79; abstraction 54.4 / 33.7 / 19.9; rising 1 / 0 / 0 | all equal | ✅ | | — |

## Groundwater reserve

| Page | Panel | Value shown (national, default period) | Recomputed in SQL | Match? | Method | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| GW reserve | KPI Districts with Aquifer Records | 52 | 52 | ✅ | | — |
| GW reserve | KPI Total Recharge | 57 592 Mm³/yr (2025) | 57 592 | ✅ | Σ (direct mm × area / 1000 + indirect m³ / 10⁶), D19 | — |
| GW reserve | KPI Total Abstraction | 1 520 | 1 520 | ✅ | | — |
| GW reserve | KPIs Dynamic / Static Reserve | 16 258 Mm³/yr; 2 683 464 Mm³ | equal | ✅ | sums of 11_1 (2025) | — |
| GW reserve | KPI Exploitation Index | 17.2 % (area-weighted mean), Low | 17.2 | ✅ | stored 11.3 index, classes on the ratio (spec §11.3) | — |
| GW reserve | KPI Districts at Risk | 3 (EI ≥ 0.5) | 3 | ✅ | | — |
| GW reserve | Chart Depth to water (11_1) | Oct 2025 23.89 … Sep 2026 19.40; long-term 22.92; trend line 27.59 → 27.89 (+3.24 m/decade) | all equal; annual means 2000 17.58 → 2024 26.78 explain the line above the recent values | ✅ | a different depth series from the Groundwater page's 10_1 telemetry (BUF 15.1 vs 37.4 m): each is labelled with its table | — |
| GW reserve | Chart Recharge and abstraction | 2025 57 591.7 / 1 520.0; 2000 46 938.3 / 1 105.4 | equal | ✅ | | — |
| GW reserve | Breakdown Reserve components / EI classes | 16 258 / null / 1 520 / null; 33 / 13 / 3 / 2 / 1 / 0 | equal | ✅ | | — |
| GW reserve | Table Groundwater balance by district | Overberg 96.9 / 18.2 / 0.844 / 33 734 Very High; Central Karoo 0.667 High; NMA 0.514 High; CPT 125.9 / 54.4 / 0.314 / 33 970 | equal | ✅ | | — |
| GW reserve | Maps ei / recharge / gw-level (11_1) / SGI | BUF 0.186 / 48 / 15.1; CPT 0.314 / 22 / 28.0; DC1 0.178 / 19 / 28.0 | equal | ✅ | | — |
| GW reserve | Entity card DC44 | EI 0.032; extent 10 742 km²; thickness 63.3 m; transmissivity 161.4; specific yield 0.155; static 66 314; dynamic 397; recharge 79; yield 1.8; depletion 0.26 | equal | ✅ | | — |
| GW reserve | Districts CPT / DC34 / DC44 | recharge 126 / 640 / 1 013; abstraction 54 / 34 / 20; dynamic 178 / 177 / 397; static 33 970 / 39 328 / 66 314; EI 31.4 / 23.6 / 3.2 | all equal | ✅ | | — |

## Water quality

| Page | Panel | Value shown (national, default period) | Recomputed in SQL | Match? | Method | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| Water quality | KPI Compliance Rate | 94 % (Sep 2026, 14 parameters × 52 districts) | 94.4 % of 728 tests | ✅ | guideline table `wq_guidelines`; `scripts/audit_step1/wq_check.py` (independent CCME / guideline code) | — |
| Water quality | KPI Districts Exceeding Limits | 32 | 32 (20 compliant) | ✅ | | — |
| Water quality | KPI Average WQI | 91, Excellent | 91.22 (area-weighted CCME WQI of the 52 districts over Oct 2025 – Sep 2026) | ✅ | CCME 2001: F1 scope, F2 frequency, F3 amplitude | — |
| Water quality | KPIs Poor / High-risk districts | 0 / 0 | 0 / 0 (all 52 ≥ 90) | ✅ | | — |
| Water quality | Charts Parameter by month (9 parameters) | EC Jan 2026 79.746, Sep 69.321, Jan 2025 87.966, Dec 74.871; pH Sep 7.538; nitrate 2.958; E. coli 99.54 | equal | ✅ | stored monthly values, area-weighted | — |
| Water quality | Chart WQI (CCME) | Oct 2025 91.3 … Sep 2026 91.2; long-term 91.3 | all 12 equal | ✅ | | — |
| Water quality | Charts Groups (nutrients / metals / micro) | nitrate 3.065 → 2.958, orthophosphate 0.108, ammonia 0.247; Pb/Cd/Cr 2.713; E. coli 99.54, coliforms 348.5 | equal | ✅ | combined metal columns shown as stored, never compared with a single-metal limit | — |
| Water quality | Breakdowns Compliance / WQI distribution / by province / per parameter | 20 / 32; 52 Excellent; EC 8 … WC 6; 100 % for the first 8 parameters | equal | ✅ | | — |
| Water quality | Breakdown Parameter range | EC 47.17 / 76.669 / 118.35 … E. coli 18.85 / 96.002 / 156.4; guidelines 150 / 25 / 6 / 0.125 / 1 / 75 / 1000 | equal | ✅ | min / plain mean / max over the 52 district values (a range of districts, not a scope value) | — |
| Water quality | Table Latest results (Sep 2026) | pH 7.538, EC 69.321, turbidity 6.864, DO 7.721, BOD₅ 2.967, COD 14.836 … all within guideline | equal | ✅ | | — |
| Water quality | Table Parameters outside guideline | Alfred Nzo orthophosphate 0.21 (1.7 ×), eThekwini 0.202, uMgungundlovu 0.202, uMzinyathi 0.191 | equal | ✅ | | — |
| Water quality | Table Districts by WQI (poorest / best) | Xhariep 90 (−0.5), West Coast 90 (−0.9), Garden Route 91 (−1.0); Dr Kenneth Kaunda 96 (+4.2), Sekhukhune 92 (−3.6), Dr Ruth S. Mompati 92 (+0.1) | equal, including the change vs the WQI 12 months earlier | ✅ | | — |
| Water quality | Maps WQI and × guideline (pH, DO, EC, turbidity, nitrate, phosphate, ammonia, BOD₅, COD, E. coli) | BUF nitrate 0.22, phosphate 0.34, DO 0.76, pH 0.78, E. coli 0.08; CPT phosphate 1.40; WQI BUF 91, DC40 96, DC16 90 | equal | ✅ | DO: guideline ÷ value | — |
| Water quality | Entity card DC44 | WQI 91; EC 49.73, pH 8.3, turbidity 2.95, DO 9.5, nitrate 5.5, orthophosphate 0.21, E. coli 50.95 | equal | ✅ | | — |
| Water quality | Forecast tab: WQI / parameters / E. coli, indicators, band outlook | histories = the dashboard series (WQI 91.2, nitrate 2.958, orthophosphate 0.108, turbidity 6.86, E. coli 99.5 → 100 at 0 dp) | | ✅ | see "Forecast tabs" | — |
| Water quality | Districts CPT / DC34 / DC44 | compliance 86 / 93 / 93 % (CPT fails orthophosphate and chlorophyll-a; the others orthophosphate); WQI 91 ×3 | equal | ✅ | | — |
