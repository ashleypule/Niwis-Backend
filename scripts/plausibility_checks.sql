-- Plausibility checks before serving columns in the database view (docs/DATA_GAPS.md §15), 2026-10-15.
-- Read-only. Run: python scripts/run_checks.py scripts/plausibility_checks.sql
-- Blocks: a title line, then SQL; blocks separated by lines containing only ---.
Relative humidity and solar radiation: range and number of distinct values
SELECT min(relative_humidity_pct), max(relative_humidity_pct), count(DISTINCT relative_humidity_pct) AS rh_distinct,
       min(solar_radiation_wm2), max(solar_radiation_wm2), count(DISTINCT solar_radiation_wm2) AS rad_distinct
FROM meteorological_drought_master
---
Monthly climatology of humidity and radiation in four contrasting districts (radiation identical everywhere)
SELECT district_name, month, round(avg(relative_humidity_pct)::numeric, 1) AS rh, round(avg(solar_radiation_wm2)::numeric, 1) AS rad
FROM meteorological_drought_master
WHERE district_name IN ('City of Cape Town', 'City of Johannesburg', 'eThekwini', 'Namakwa')
GROUP BY 1, 2 ORDER BY 1, 2
---
Mean daily correlation of humidity / radiation with rainfall and Tmax (per district, averaged)
SELECT round(avg(r_rh)::numeric, 2) AS r_rh_rain, round(avg(r_rad_t)::numeric, 2) AS r_rad_tmax
FROM (SELECT district_name, corr(relative_humidity_pct, daily_rainfall_mm_day) AS r_rh, corr(solar_radiation_wm2, tmax_c) AS r_rad_t
      FROM meteorological_drought_master GROUP BY 1) x
---
GRACE TWS and surface water extent: range per year (GRACE data start in April 2002)
SELECT year, round(min(grace_tws)::numeric, 1) AS grace_min, round(max(grace_tws)::numeric, 1) AS grace_max,
       round(min(surface_water_extent_km2)::numeric, 3) AS sw_min, round(max(surface_water_extent_km2)::numeric, 1) AS sw_max
FROM satellite_remote_sensing GROUP BY 1 ORDER BY 1
---
Surface water extent by district (largest means)
SELECT district_name, round(avg(surface_water_extent_km2)::numeric, 1) AS mean, round(min(surface_water_extent_km2)::numeric, 2) AS min,
       round(max(surface_water_extent_km2)::numeric, 1) AS max
FROM satellite_remote_sensing GROUP BY 1 ORDER BY 2 DESC LIMIT 8
---
Rainfall intensity, CHIRPS and TAMSAT against the gauge series (6.1)
SELECT count(DISTINCT "Hourly_Rainfall_Intensity_mm_h") AS intensity_distinct,
       round(corr("Hourly_Rainfall_Intensity_mm_h", "Daily_Rainfall_Total_mm")::numeric, 3) AS r_intensity,
       round(min("Hourly_Rainfall_Intensity_mm_h" / NULLIF("Daily_Rainfall_Total_mm", 0))::numeric, 3) AS intensity_ratio_min,
       round(max("Hourly_Rainfall_Intensity_mm_h" / NULLIF("Daily_Rainfall_Total_mm", 0))::numeric, 3) AS intensity_ratio_max,
       round(corr("CHIRPS_Daily_Rainfall_Estimate_mm_day", "Daily_Rainfall_Total_mm")::numeric, 3) AS r_chirps,
       round(min("CHIRPS_Daily_Rainfall_Estimate_mm_day" / NULLIF("Daily_Rainfall_Total_mm", 0))::numeric, 3) AS chirps_ratio_min,
       round(max("CHIRPS_Daily_Rainfall_Estimate_mm_day" / NULLIF("Daily_Rainfall_Total_mm", 0))::numeric, 3) AS chirps_ratio_max
FROM "6.1_core_rainfall_variables_master"
---
TAMSAT: distinct values per district-month and relation to the monthly total
SELECT round(avg(nd)::numeric, 3) AS distinct_per_month, max(nd), round(corr(t, m)::numeric, 3) AS r_monthly_total,
       round(avg(t * 3 / NULLIF(m, 0))::numeric, 3) AS ratio_x3
FROM (SELECT "District_Name", "Year", "Month", count(DISTINCT "TAMSAT_Satellite_Rainfall_Estimate_mm_dekad") AS nd,
             max("TAMSAT_Satellite_Rainfall_Estimate_mm_dekad") AS t, max("Monthly_Total_Precipitation_mm") AS m
      FROM "6.1_core_rainfall_variables_master" GROUP BY 1, 2, 3) x
---
6.1 seasonal totals (OND/JFM/AMJ/JAS) against sums of daily rainfall, 2000-2025
WITH m AS (SELECT "District_Name" dn, "Year" y, "Month" mo, sum("Daily_Rainfall_Total_mm") s FROM "6.1_core_rainfall_variables_master" GROUP BY 1, 2, 3),
st AS (SELECT "District_Name" dn, "Year" y, max(NULLIF("Seasonal_Total_OND_mm", '')::float) ond, max("Seasonal_Total_JFM_mm") jfm,
              max("Seasonal_Total_AMJ_mm") amj, max("Seasonal_Total_JAS_mm") jas FROM "6.1_core_rainfall_variables_master" GROUP BY 1, 2)
SELECT count(*),
       round(avg(abs(jfm - (SELECT sum(s) FROM m WHERE m.dn = st.dn AND m.y = st.y AND mo IN (1, 2, 3))))::numeric, 2) AS jfm_mad,
       round(avg(abs(amj - (SELECT sum(s) FROM m WHERE m.dn = st.dn AND m.y = st.y AND mo IN (4, 5, 6))))::numeric, 2) AS amj_mad,
       round(avg(abs(jas - (SELECT sum(s) FROM m WHERE m.dn = st.dn AND m.y = st.y AND mo IN (7, 8, 9))))::numeric, 2) AS jas_mad,
       round(avg(abs(ond - (SELECT sum(s) FROM m WHERE m.dn = st.dn AND m.y = st.y AND mo IN (10, 11, 12))))::numeric, 2) AS ond_mad
FROM st WHERE y BETWEEN 2000 AND 2025
---
Reservoirs: frozen since? (latest month in which any district's storage differs from its final value)
WITH last AS (SELECT DISTINCT ON (district_name) district_name, dam_storage_million_m3 v FROM hydrological_drought_master ORDER BY district_name, date DESC),
diff AS (SELECT h.district_name, max(h.date) FILTER (WHERE h.dam_storage_million_m3 <> l.v) last_diff
         FROM hydrological_drought_master h JOIN last l USING (district_name) GROUP BY 1)
SELECT min(last_diff), max(last_diff), count(*) AS districts FROM diff
---
Reservoirs: national totals around the freeze, and inflow = outflow afterwards
SELECT date, round(sum(dam_storage_million_m3)::numeric, 0) AS storage_mm3, round(sum(dam_inflow_m3_s)::numeric, 2) AS inflow,
       round(sum(dam_outflow_m3_s)::numeric, 2) AS outflow, count(*) FILTER (WHERE dam_inflow_m3_s = dam_outflow_m3_s) AS in_eq_out
FROM hydrological_drought_master WHERE date BETWEEN '2021-05-01' AND '2022-03-01' GROUP BY 1 ORDER BY 1
---
Reservoirs: district-months at 0 % / 100 % FSC; national mean % FSC in July of each year
SELECT (SELECT count(*) FROM hydrological_drought_master WHERE dam_level_pct_fsc = 0) AS zero_months,
       (SELECT count(*) FROM hydrological_drought_master WHERE dam_level_pct_fsc >= 99.99) AS full_months,
       (SELECT string_agg(extract(year FROM date) || ':' || round(p::numeric, 1), ', ' ORDER BY date)
        FROM (SELECT date, avg(dam_level_pct_fsc) p FROM hydrological_drought_master WHERE extract(month FROM date) = 7 GROUP BY 1) j) AS july_mean_pct
---
4_1 heatwave frequency and duration against daily Tmax (> 35 °C), per district-year
WITH d AS (SELECT "District_Name" dn, "Date"::date dd, "Tmax_C" tx, "Heatwave_Frequency_Days" hf, "Heatwave_Duration_Max_Consecutive_Days" hd FROM "4_1_temperature_change_master"),
r AS (SELECT dn, dd, tx, dd - (row_number() OVER (PARTITION BY dn, (tx > 35) ORDER BY dd))::int grp FROM d),
runs AS (SELECT dn, grp, min(dd) s, count(*) len FROM r WHERE tx > 35 GROUP BY 1, 2),
calc AS (SELECT dn, extract(year FROM s)::int y, max(len) mx FROM runs GROUP BY 1, 2),
stored AS (SELECT dn, extract(year FROM dd)::int y, max(hd) hd, max(hf) hf, count(*) FILTER (WHERE tx > 35) n35 FROM d GROUP BY 1, 2)
SELECT count(*) AS district_years, sum((stored.n35 = stored.hf)::int) AS frequency_equal, sum((coalesce(calc.mx, 0) = stored.hd)::int) AS duration_equal
FROM stored LEFT JOIN calc USING (dn, y)
---
4_1 LST against air temperature
SELECT min("LST_Day_C"), max("LST_Day_C"), round(corr("LST_Day_C", "Tmax_C")::numeric, 3) AS r_day_tmax,
       round(avg("LST_Day_C" - "Tmax_C")::numeric, 2) AS day_minus_tmax, round(avg("LST_Night_C" - "Tmin_C")::numeric, 2) AS night_minus_tmin,
       sum(("LST_Day_C" < "LST_Night_C")::int) AS day_below_night, count(*)
FROM "4_1_temperature_change_master"
---
4_2 ETCCDI indices recomputed from 6.1 daily rainfall, 2001-2025
WITH d AS (SELECT "District_Name" dn, "Year" y, "Daily_Rainfall_Total_mm" r FROM "6.1_core_rainfall_variables_master" WHERE "Year" BETWEEN 2001 AND 2025),
c AS (SELECT dn, y, count(*) FILTER (WHERE r >= 10) r10, count(*) FILTER (WHERE r >= 20) r20, sum(r) FILTER (WHERE r >= 1) prcptot, max(r) rx1,
             count(*) FILTER (WHERE r >= 1) wet FROM d GROUP BY 1, 2)
SELECT count(*) AS district_years, sum((c.r10 = t."R10mm_Days")::int) AS r10_equal, sum((c.r20 = t."R20mm_Days")::int) AS r20_equal,
       round(avg(abs(c.prcptot - t."PRCPTOT_mm"))::numeric, 2) AS prcptot_mad, round(avg(abs(c.rx1 - t."Rx1day_mm"))::numeric, 3) AS rx1_mad,
       round(avg(abs(c.prcptot / NULLIF(c.wet, 0) - t."SDII_mm_per_wet_day"))::numeric, 3) AS sdii_mad
FROM c JOIN "4_2_wet_dry_spells_master" t ON t."District_Name" = c.dn AND t."Year" = c.y
---
4_2 CDD / CWD against the longest dry / wet runs in 6.1 (runs counted in the year they start)
WITH d AS (SELECT "District_Name" dn, "Date"::date dd, "Daily_Rainfall_Total_mm" r FROM "6.1_core_rainfall_variables_master" WHERE "Year" BETWEEN 2001 AND 2025),
x AS (SELECT dn, dd, (r >= 1) wet, dd - (row_number() OVER (PARTITION BY dn, (r >= 1) ORDER BY dd))::int grp FROM d),
runs AS (SELECT dn, wet, grp, extract(year FROM min(dd))::int y, count(*) len FROM x GROUP BY 1, 2, 3),
calc AS (SELECT dn, y, max(len) FILTER (WHERE NOT wet) cdd, max(len) FILTER (WHERE wet) cwd FROM runs GROUP BY 1, 2)
SELECT count(*), sum((c.cdd = t."CDD_Days")::int) AS cdd_equal, sum((c.cwd = t."CWD_Days")::int) AS cwd_equal
FROM calc c JOIN "4_2_wet_dry_spells_master" t ON t."District_Name" = c.dn AND t."Year" = c.y
---
4_3 MAP and seasons against 6.1 sums (DJF = Jan + Feb + Dec of the same year)
WITH m AS (SELECT "District_Name" dn, "Year" y, "Month" mo, sum("Daily_Rainfall_Total_mm") s FROM "6.1_core_rainfall_variables_master" GROUP BY 1, 2, 3)
SELECT count(*),
       round(avg(abs(t."MAP_mm" - (SELECT sum(s) FROM m WHERE m.dn = t."District_Name" AND m.y = t."Year")))::numeric, 2) AS map_mad,
       round(avg(abs(t."Precip_DJF_mm" - (SELECT sum(s) FROM m WHERE m.dn = t."District_Name" AND m.y = t."Year" AND mo IN (1, 2, 12))))::numeric, 2) AS djf_same_year_mad
FROM "4_3_mean_annual_precipitation_master" t WHERE t."Year" BETWEEN 2001 AND 2025
---
4_3 record statistics: stored values against 2000-2025 (stored = computed over 2000-2026 incl. the partial year)
SELECT "District_Name", max("Precip_CV_Percent") AS cv_stored,
       round((100 * stddev_samp("MAP_mm") FILTER (WHERE "Year" <= 2025) / avg("MAP_mm") FILTER (WHERE "Year" <= 2025))::numeric, 2) AS cv_2000_2025,
       round((100 * stddev_samp("MAP_mm") / avg("MAP_mm"))::numeric, 2) AS cv_2000_2026,
       max("ENSO_Nino34_Correlation") AS enso_stored,
       round(corr("MAP_mm", "Nino34_Annual_Mean") FILTER (WHERE "Year" <= 2025)::numeric, 3) AS enso_2000_2025,
       max("MK_Sens_Slope_mm_yr") AS sen_stored, max("MK_p_value") AS p_stored
FROM "4_3_mean_annual_precipitation_master"
WHERE "District_Name" IN ('Alfred Nzo', 'Namakwa', 'eThekwini', 'City of Cape Town') GROUP BY 1
---
4_3 Niño 3.4 annual mean per year
SELECT "Year", min("Nino34_Annual_Mean"), max("Nino34_Annual_Mean") FROM "4_3_mean_annual_precipitation_master" GROUP BY 1 ORDER BY 1
---
4_6 streamflow anomaly against the MAP anomaly; elasticity and ratio ranges
SELECT round(corr(t."Streamflow_Anomaly_percent", r.anom)::numeric, 3) AS r_with_map_anomaly,
       min(t."Climate_Streamflow_Elasticity"), max(t."Climate_Streamflow_Elasticity"), min(t."Rainfall_Runoff_Ratio"), max(t."Rainfall_Runoff_Ratio")
FROM "4_6_streamflow_and_climate_linkage_annual _master" t
JOIN (SELECT "District_Name" dn, "Year" y, 100 * ("MAP_mm" - avg("MAP_mm") OVER (PARTITION BY "District_Name")) / avg("MAP_mm") OVER (PARTITION BY "District_Name") anom
      FROM "4_3_mean_annual_precipitation_master") r ON r.dn = t."District_Name" AND r.y = t."Year"
---
5.2 GDD against Σ max(Tavg - 10, 0) from 4_1 daily values, 2015
WITH d AS (SELECT "District_Name" dn, left("Date", 4)::int y, substr("Date", 6, 2)::int mo, sum(greatest("Tavg_C" - 10, 0)) g10
           FROM "4_1_temperature_change_master" WHERE "Date" BETWEEN '2015-01-01' AND '2015-12-31' GROUP BY 1, 2, 3)
SELECT count(*), round(avg(abs(g.g10 - s."Growing_Degree_Days_GDD_C"))::numeric, 2) AS mad
FROM d g JOIN "5.2_meteorological_drivers_saws_master" s ON s."District_Name" = g.dn AND s."Year" = g.y AND s."Month" = g.mo
---
6.2 RAI range and relation to SPI-1
SELECT min("RAI_Rainfall_Anomaly_Index"), max("RAI_Rainfall_Anomaly_Index"),
       round(corr("RAI_Rainfall_Anomaly_Index", "SPI_1_Meteorological_Drought_Onset")::numeric, 3) AS r_spi1
FROM "6.2_rainfall_derived_drought_indices_master"
---
meteorological seasonal_rainfall_mm_season: one value per district-year (= calendar-year total)
SELECT count(DISTINCT (district_name, year, seasonal_rainfall_mm_season)) AS distinct_values, count(DISTINCT (district_name, year)) AS district_years,
       (SELECT max(seasonal_rainfall_mm_season) FROM meteorological_drought_master WHERE district_name = 'Alfred Nzo' AND year = 2014) AS alfred_nzo_2014,
       (SELECT round(sum(daily_rainfall_mm_day)::numeric, 2) FROM meteorological_drought_master WHERE district_name = 'Alfred Nzo' AND year = 2014) AS alfred_nzo_2014_sum
FROM meteorological_drought_master
---
runoff_variables_master soil moisture prior
SELECT min("Soil_Moisture_Prior_m3m3"), max("Soil_Moisture_Prior_m3m3"), count(DISTINCT "Soil_Moisture_Prior_m3m3") FROM runoff_variables_master
---
7.2 ranges and constants (added 2026-10-20, DATA_GAPS §20)
SELECT min("Runoff_Anomaly_percent"), max("Runoff_Anomaly_percent"), count(DISTINCT "Runoff_Ratio_Trend_percent_per_yr") AS trend_distinct,
       count(DISTINCT "Hydrological_Response_Time_hours") AS response_distinct,
       min("SSI_Standardized_Streamflow_Index") AS ssi_min, max("SSI_Standardized_Streamflow_Index") AS ssi_max,
       min("Year" * 100 + "Month") AS first_month, max("Year" * 100 + "Month") AS last_month, count(DISTINCT "District_Name") AS districts
FROM "7.2_derived_runoff_indicators_master"
---
7.2 runoff anomaly against the same-month mean of all months of runoff_variables_master (expected r = 1.0)
WITH m AS (SELECT "District_Name" dn, "Year" y, "Month" mo, sum("Daily_Surface_Runoff_Volume_m3_day") v FROM runoff_variables_master GROUP BY 1, 2, 3),
     n AS (SELECT dn, mo, avg(v) nv FROM m GROUP BY 1, 2)
SELECT count(*), round(corr(a."Runoff_Anomaly_percent", (m.v - n.nv) / n.nv * 100)::numeric, 5) AS r,
       round(avg(abs(a."Runoff_Anomaly_percent" - (m.v - n.nv) / n.nv * 100))::numeric, 3) AS mad_pp
FROM m JOIN n USING (dn, mo) JOIN "7.2_derived_runoff_indicators_master" a ON a."District_Name" = m.dn AND a."Year" = m.y AND a."Month" = m.mo
WHERE n.nv > 0
---
7.2 SSI against 6.2 SPI-3 (expected r about 0.996: a rainfall index, not a streamflow one)
SELECT round(corr(a."SSI_Standardized_Streamflow_Index", s."SPI_3_Short_Term_Agricultural_Drought")::numeric, 3) AS r_spi3
FROM "7.2_derived_runoff_indicators_master" a JOIN "6.2_rainfall_derived_drought_indices_master" s
  ON s."District_Name" = a."District_Name" AND s."Year" = a."Year" AND s."Month" = a."Month"
---
7.2 2-year flood flow against the month's peak flow in runoff_variables_master (expected ratio 0.93, constant)
WITH p AS (SELECT "District_Name" dn, "Year" y, "Month" mo, max("Peak_Flow_Rates_m3s") pk FROM runoff_variables_master GROUP BY 1, 2, 3)
SELECT round(corr(a."Flood_Flow_2yr_m3s", p.pk)::numeric, 4) AS r, round(avg(a."Flood_Flow_2yr_m3s" / p.pk)::numeric, 4) AS ratio,
       round(stddev(a."Flood_Flow_2yr_m3s" / p.pk)::numeric, 4) AS ratio_sd,
       round(avg(a."Flood_Flow_100yr_m3s" / nullif(a."Flood_Flow_2yr_m3s", 0))::numeric, 3) AS ratio_100_2
FROM p JOIN "7.2_derived_runoff_indicators_master" a ON a."District_Name" = p.dn AND a."Year" = p.y AND a."Month" = p.mo
WHERE p.pk > 0
