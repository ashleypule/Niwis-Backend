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
