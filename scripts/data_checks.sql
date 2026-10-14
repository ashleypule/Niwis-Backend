district names per table (normalised lower/trim) vs meteorological set
WITH m AS (SELECT DISTINCT lower(trim(district_name)) d FROM meteorological_drought_master),
t AS (
 SELECT 'satellite' t, lower(trim(district_name)) d FROM satellite_remote_sensing UNION
 SELECT 'hydro', lower(trim(district_name)) FROM hydrological_drought_master UNION
 SELECT '4_1', lower(trim("District_Name")) FROM "4_1_temperature_change_master" UNION
 SELECT '4_2', lower(trim("District_Name")) FROM "4_2_wet_dry_spells_master" UNION
 SELECT '4_3', lower(trim("District_Name")) FROM "4_3_mean_annual_precipitation_master" UNION
 SELECT '4_4', lower(trim("District_Name")) FROM "4_4_potential_evaporation_daily_master" UNION
 SELECT '4_5', lower(trim("District_Name")) FROM "4_5_irrigation_demand_daily_master" UNION
 SELECT '4_6', lower(trim("District_Name")) FROM "4_6_streamflow_and_climate_linkage_annual _master" UNION
 SELECT '5.1', lower(trim("District_Name")) FROM "5.1_primary_satellite_vegetation_variables_sansa_master" UNION
 SELECT '5.2', lower(trim("District_Name")) FROM "5.2_meteorological_drivers_saws_master" UNION
 SELECT '6.1', lower(trim("District_Name")) FROM "6.1_core_rainfall_variables_master" UNION
 SELECT '6.2', lower(trim("District_Name")) FROM "6.2_rainfall_derived_drought_indices_master" UNION
 SELECT 'runoff', lower(trim("District_Name")) FROM runoff_variables_master)
SELECT t, count(*) n, count(*) FILTER (WHERE d NOT IN (SELECT d FROM m)) not_in_met, string_agg(d, ', ') FILTER (WHERE d NOT IN (SELECT d FROM m)) extra FROM t GROUP BY t ORDER BY t
---
exact-case differences (names that differ only by case/spacing from meteorological)
SELECT DISTINCT s.district_name sat_name, m.district_name met_name FROM satellite_remote_sensing s JOIN (SELECT DISTINCT district_name FROM meteorological_drought_master) m ON lower(trim(s.district_name)) = lower(trim(m.district_name)) WHERE s.district_name <> m.district_name LIMIT 5
---
all 52 district names (meteorological)
SELECT string_agg(district_name, ' | ' ORDER BY district_name) FROM (SELECT DISTINCT district_name FROM meteorological_drought_master) s
---
is SPI constant within a month on daily rows?
SELECT max(n) max_distinct_spi3_per_month, max(nr) max_distinct_monthly_rain_per_month, max(nt) max_distinct_temp_anom_per_month FROM (SELECT district_name, year, month, count(DISTINCT spi_3) n, count(DISTINCT monthly_rainfall_mm_month) nr, count(DISTINCT temp_anomaly_c) nt FROM meteorological_drought_master GROUP BY 1,2,3) s
---
monthly_rainfall vs sum of daily rainfall
SELECT district_name, year, month, round(sum(daily_rainfall_mm_day)::numeric,2) sum_daily, max(monthly_rainfall_mm_month) monthly_col, count(*) days FROM meteorological_drought_master WHERE year=2024 AND month IN (1,7,12) AND district_name IN ('Alfred Nzo','City of Cape Town','Vhembe') GROUP BY 1,2,3 ORDER BY 1,2,3
---
6.2 SPI-6 vs meteorological spi_6
SELECT count(*) compared, count(*) FILTER (WHERE abs(m.spi_6 - s."SPI_6_Medium_Term_Hydrological_Impacts") > 1e-6) differ FROM (SELECT DISTINCT ON (district_name, year, month) district_name, year, month, spi_6 FROM meteorological_drought_master ORDER BY district_name, year, month, date) m JOIN "6.2_rainfall_derived_drought_indices_master" s ON lower(s."District_Name")=lower(m.district_name) AND s."Year"=m.year AND s."Month"=m.month
---
6.1 daily rainfall vs meteorological daily rainfall
SELECT count(*) compared, count(*) FILTER (WHERE abs(m.daily_rainfall_mm_day - r."Daily_Rainfall_Total_mm") > 1e-6) differ FROM meteorological_drought_master m JOIN "6.1_core_rainfall_variables_master" r ON r."District_Name"=m.district_name AND r."Date"=m.date::text WHERE m.year=2024
---
newest dates per table
SELECT 'met' t, max(date)::text FROM meteorological_drought_master UNION ALL SELECT 'sat', max(date)::text FROM satellite_remote_sensing UNION ALL SELECT 'hydro', max(date)::text FROM hydrological_drought_master UNION ALL SELECT '4_1', max("Date") FROM "4_1_temperature_change_master" UNION ALL SELECT '4_4', max("Date") FROM "4_4_potential_evaporation_daily_master" UNION ALL SELECT '5.1', max("Date") FROM "5.1_primary_satellite_vegetation_variables_sansa_master" UNION ALL SELECT '5.2', max(make_date("Year"::int,"Month"::int,1))::text FROM "5.2_meteorological_drivers_saws_master" UNION ALL SELECT '6.1', max("Date") FROM "6.1_core_rainfall_variables_master" UNION ALL SELECT '6.2', max(make_date("Year"::int,"Month"::int,1))::text FROM "6.2_rainfall_derived_drought_indices_master" UNION ALL SELECT 'runoff', max("Date")::text FROM runoff_variables_master
---
missing calendar days in 2024 (meteorological, one district)
SELECT d::date FROM generate_series('2024-01-01'::date,'2024-12-31','1 day') d EXCEPT SELECT date FROM meteorological_drought_master WHERE district_name='Alfred Nzo' ORDER BY 1
---
ET0 comparison, Alfred Nzo Jan 2024
SELECT (SELECT round(avg(et0_mm_day)::numeric,3) FROM meteorological_drought_master WHERE district_name='Alfred Nzo' AND year=2024 AND month=1) met_et0_day_avg, (SELECT round(avg("Daily_ET0_mm")::numeric,4) FROM "4_4_potential_evaporation_daily_master" WHERE "District_Name"='Alfred Nzo' AND "Date" LIKE '2024-01-%') t44_et0_day_avg, (SELECT round(sum("Daily_ET0_mm")::numeric,3) FROM "4_4_potential_evaporation_daily_master" WHERE "District_Name"='Alfred Nzo' AND "Date" LIKE '2024-01-%') t44_month_sum, (SELECT "Potential_Evapotranspiration_ET0_mm" FROM "5.2_meteorological_drivers_saws_master" WHERE "District_Name"='Alfred Nzo' AND "Year"=2024 AND "Month"=1) t52_et0_month
---
non-numeric values in text-typed numeric columns
SELECT 'SPEI_Daily' c, "SPEI_Daily" v, count(*) FROM "4_4_potential_evaporation_daily_master" WHERE "SPEI_Daily" !~ '^-?[0-9.]+(e-?[0-9]+)?$' GROUP BY 2 UNION ALL SELECT 'OND', "Seasonal_Total_OND_mm", count(*) FROM "6.1_core_rainfall_variables_master" WHERE "Seasonal_Total_OND_mm" !~ '^-?[0-9.]+$' GROUP BY 2
---
runoff: daily volume sum vs monthly total, Alfred Nzo Jan 2024
SELECT round((sum("Daily_Surface_Runoff_Volume_m3_day")/1e6)::numeric,2) sum_daily_Mm3, max("Monthly_Runoff_Totals_Million_m3_month") monthly_Mm3, round(sum("Mean_Catchment_Rainfall_mm")::numeric,1) rain_mm, max("Catchment_Area_km2") area_km2, round(avg("Peak_Flow_Rates_m3s")::numeric,1) avg_peak FROM runoff_variables_master WHERE "District_Name"='Alfred Nzo' AND "Year"=2024 AND "Month"=1
---
hydro: largest dam storage districts, 2025-12
SELECT district_name, river_discharge_m3_s, dam_storage_million_m3, dam_level_pct_fsc, dam_inflow_m3_s, borehole_water_level_m_bgl, sgi_dimensionless, surface_runoff_m3_s FROM hydrological_drought_master WHERE date='2025-12-01' ORDER BY dam_storage_million_m3 DESC LIMIT 8
---
hydro: national dam totals over time (implied FSC)
SELECT date, round(sum(dam_storage_million_m3)::numeric) storage, round((sum(dam_storage_million_m3) / nullif(sum(dam_storage_million_m3 / nullif(dam_level_pct_fsc,0) * 100),0) * 100)::numeric,1) implied_pct FROM hydrological_drought_master WHERE date IN ('2015-01-01','2020-01-01','2025-12-01') GROUP BY 1 ORDER BY 1
---
4_3 Nino34 outliers
SELECT "Year", min("Nino34_Annual_Mean"), max("Nino34_Annual_Mean") FROM "4_3_mean_annual_precipitation_master" GROUP BY 1 HAVING min("Nino34_Annual_Mean") < -5 ORDER BY 1
---
5.1: rows per district-day, NDVI variation within month
SELECT count(*) n_rows, count(DISTINCT ("District_Name","Date")) district_days, (SELECT max(n) FROM (SELECT count(DISTINCT "NDVI_10day_composite") n FROM "5.1_primary_satellite_vegetation_variables_sansa_master" GROUP BY "District_Name","Year","Month") s) max_ndvi_values_per_month FROM "5.1_primary_satellite_vegetation_variables_sansa_master"
---
satellite_remote_sensing vs 5.1 NDVI, Alfred Nzo Jan 2024
SELECT (SELECT ndvi FROM satellite_remote_sensing WHERE district_name='alfred nzo' AND date='2024-01-01') sat_ndvi, (SELECT avg("NDVI_10day_composite") FROM "5.1_primary_satellite_vegetation_variables_sansa_master" WHERE "District_Name"='Alfred Nzo' AND "Year"=2024 AND "Month"=1) t51_ndvi_avg, (SELECT vci FROM satellite_remote_sensing WHERE district_name='alfred nzo' AND date='2024-01-01') sat_vci, (SELECT avg("VCI_percent") FROM "5.1_primary_satellite_vegetation_variables_sansa_master" WHERE "District_Name"='Alfred Nzo' AND "Year"=2024 AND "Month"=1) t51_vci_avg
---
4_6: national MAR sum per year (m3 -> Mm3)
SELECT "Year", round((sum("MAR_m3_year")/1e6)::numeric) national_mar_Mm3 FROM "4_6_streamflow_and_climate_linkage_annual _master" WHERE "Year" IN (2010, 2020, 2025) GROUP BY 1 ORDER BY 1
---
roles and privileges
SELECT r.rolname, r.rolcanlogin, (SELECT count(*) FROM information_schema.role_table_grants g WHERE g.grantee=r.rolname AND g.table_schema='public' AND g.privilege_type='SELECT') public_select_grants FROM pg_roles r WHERE r.rolname IN ('supabase_read_only_user','anon','authenticated','service_role','postgres')
---
RLS policies
SELECT tablename, policyname, roles::text, cmd FROM pg_policies WHERE schemaname='public' ORDER BY 1

---
4_1: which columns are constant within a month / year (max distinct per district-month and per district-year)
SELECT max(hf_m) heatwave_freq_per_month, max(hd_m) heatwave_dur_per_month, max(ta_m) temp_anom_per_month FROM (SELECT count(DISTINCT "Heatwave_Frequency_Days") hf_m, count(DISTINCT "Heatwave_Duration_Max_Consecutive_Days") hd_m, count(DISTINCT "Temperature_Anomaly_C") ta_m FROM "4_1_temperature_change_master" GROUP BY "District_Name", left("Date",7)) s
---
4_1: heatwave frequency per district-year (constant within year?)
SELECT max(n) FROM (SELECT count(DISTINCT "Heatwave_Frequency_Days") n FROM "4_1_temperature_change_master" GROUP BY "District_Name", left("Date",4)) s
---
4_1 vs meteorological temperatures identical?
SELECT count(*) compared, count(*) FILTER (WHERE abs(t."Tmax_C"-m.tmax_c)>1e-6 OR abs(t."Tmin_C"-m.tmin_c)>1e-6) differ FROM meteorological_drought_master m JOIN "4_1_temperature_change_master" t ON t."District_Name"=m.district_name AND t."Date"=m.date::text WHERE m.year=2024
---
hot days Tmax>35 per year computed from daily vs heatwave freq (Vhembe 2024)
SELECT (SELECT count(*) FROM "4_1_temperature_change_master" WHERE "District_Name"='Vhembe' AND "Date" LIKE '2024-%' AND "Tmax_C">35) hot_days_35, (SELECT max("Heatwave_Frequency_Days") FROM "4_1_temperature_change_master" WHERE "District_Name"='Vhembe' AND "Date" LIKE '2024-%') heatwave_freq_max
---
4_2 R20 / CDD vs computed from daily rain (Alfred Nzo 2024)
SELECT (SELECT "R20mm_Days" FROM "4_2_wet_dry_spells_master" WHERE "District_Name"='Alfred Nzo' AND "Year"=2024) r20_table, (SELECT count(*) FROM meteorological_drought_master WHERE district_name='Alfred Nzo' AND year=2024 AND daily_rainfall_mm_day>=20) r20_daily, (SELECT "Rx1day_mm" FROM "4_2_wet_dry_spells_master" WHERE "District_Name"='Alfred Nzo' AND "Year"=2024) rx1_table, (SELECT max(daily_rainfall_mm_day) FROM meteorological_drought_master WHERE district_name='Alfred Nzo' AND year=2024) rx1_daily, (SELECT "PRCPTOT_mm" FROM "4_2_wet_dry_spells_master" WHERE "District_Name"='Alfred Nzo' AND "Year"=2024) prcptot, (SELECT round(sum(daily_rainfall_mm_day)::numeric,2) FROM meteorological_drought_master WHERE district_name='Alfred Nzo' AND year=2024) sum_daily
---
4_2 / 4_3 / 4_6 partial year 2026 values
SELECT a."Year", round(avg(a."PRCPTOT_mm")::numeric,1) prcptot, round(avg(b."MAP_mm")::numeric,1) map, round(avg(c."MAR_m3_year")/1e6) mar_Mm3 FROM "4_2_wet_dry_spells_master" a JOIN "4_3_mean_annual_precipitation_master" b USING ("District_Name","Year") JOIN "4_6_streamflow_and_climate_linkage_annual _master" c USING ("District_Name","Year") WHERE a."Year" IN (2024,2025,2026) GROUP BY 1 ORDER BY 1
---
6.1: rainy days / seasonal / annual constant within month or year?
SELECT max(rd) rainy_days_per_month, max(ann) annual_per_year, max(ond) ond_per_year FROM (SELECT count(DISTINCT "Number_of_Rainy_Days_days_month") rd, 0 ann, 0 ond FROM "6.1_core_rainfall_variables_master" GROUP BY "District_Name","Year","Month" UNION ALL SELECT 0, count(DISTINCT "Annual_Total_Precipitation_mm"), count(DISTINCT "Seasonal_Total_OND_mm") FROM "6.1_core_rainfall_variables_master" GROUP BY "District_Name","Year") s
---
6.1 rainy days >= 1mm vs computed (Alfred Nzo 2024-01)
SELECT max("Number_of_Rainy_Days_days_month") table_val, count(*) FILTER (WHERE "Daily_Rainfall_Total_mm">=1) computed_ge1, count(*) FILTER (WHERE "Daily_Rainfall_Total_mm">0) computed_gt0 FROM "6.1_core_rainfall_variables_master" WHERE "District_Name"='Alfred Nzo' AND "Year"=2024 AND "Month"=1
---
6.1 anomaly baseline: what period gives the stored monthly anomaly? (mean of month over 2000-2025 vs stored)
WITH mo AS (SELECT "District_Name" d, "Year" y, "Month" m, max("Monthly_Total_Precipitation_mm") p, max("Monthly_Anomaly_mm") a FROM "6.1_core_rainfall_variables_master" GROUP BY 1,2,3)
SELECT d, m, round(avg(p) FILTER (WHERE y BETWEEN 2000 AND 2025)::numeric,3) mean_2000_2025, round(avg(p) FILTER (WHERE y BETWEEN 2000 AND 2026)::numeric,3) mean_all, round(avg(p) FILTER (WHERE y BETWEEN 2001 AND 2020)::numeric,3) mean_2001_2020, round(max(p - a) FILTER (WHERE y=2024)::numeric,3) implied_normal_2024, round(min(p - a)::numeric,3) min_implied, round(max(p-a)::numeric,3) max_implied FROM mo WHERE d='Alfred Nzo' AND m IN (1,7) GROUP BY 1,2
---
hydro: per-district variation of discharge / inflow / runoff (are they all equal across districts each month?)
SELECT count(DISTINCT date) months, max(nd) max_distinct_discharge_across_districts, max(ni) max_distinct_inflow, max(nr) max_distinct_runoff, max(nb) max_distinct_baseflow FROM (SELECT date, count(DISTINCT river_discharge_m3_s) nd, count(DISTINCT dam_inflow_m3_s) ni, count(DISTINCT surface_runoff_m3_s) nr, count(DISTINCT baseflow_separation_m3_s) nb FROM hydrological_drought_master GROUP BY date) s
---
hydro: districts with zero dam storage
SELECT count(DISTINCT district_name) districts_always_zero FROM (SELECT district_name FROM hydrological_drought_master GROUP BY 1 HAVING max(dam_storage_million_m3)=0) s
---
5.1 vs 5.2: does 5.2 duplicate 6.2 / met?
SELECT count(*) compared, count(*) FILTER (WHERE abs(a."SPI_3_month"-b."SPI_3_Short_Term_Agricultural_Drought")>1e-6) differ_spi3, count(*) FILTER (WHERE abs(a."Monthly_Rainfall_mm" - c.p)>1e-6) differ_rain FROM "5.2_meteorological_drivers_saws_master" a JOIN "6.2_rainfall_derived_drought_indices_master" b USING ("District_Name","Year","Month") JOIN (SELECT "District_Name","Year","Month", max("Monthly_Total_Precipitation_mm") p FROM "6.1_core_rainfall_variables_master" GROUP BY 1,2,3) c USING ("District_Name","Year","Month")
---
5.1: VCI vs VPI identical? VHI vs ASI identical?
SELECT count(*) FILTER (WHERE abs("VCI_percent"-"VPI_percent")>1e-9) vci_ne_vpi, count(*) FILTER (WHERE abs("VHI_percent"-(100-"ASI_percent"))>1e-6) vhi_ne_100_minus_asi, count(*) FILTER (WHERE abs("VHI_percent"-"ASI_percent")>1e-6) vhi_ne_asi FROM "5.1_primary_satellite_vegetation_variables_sansa_master"
---
5.1: LST equals tavg?
SELECT count(*) compared, count(*) FILTER (WHERE abs(v."LST_Land_Surface_Temperature_C" - m.tavg_c)>1e-6) differ FROM "5.1_primary_satellite_vegetation_variables_sansa_master" v JOIN meteorological_drought_master m ON m.district_name=v."District_Name" AND m.date::text=v."Date" WHERE m.year=2024
---
runoff: peak flow & baseflow per day plausibility, mean annual runoff depth by district (mm) using 6500 km2
SELECT "District_Name", round((sum("Daily_Surface_Runoff_Volume_m3_day")/6.5e9*1000/26.7)::numeric,1) mean_annual_runoff_mm_if_6500km2, round(avg("Runoff_Coefficient_dimensionless")::numeric,3) avg_coeff, round(max("Peak_Flow_Rates_m3s")::numeric) max_peak FROM runoff_variables_master GROUP BY 1 ORDER BY 2 DESC LIMIT 5
---
runoff: is Daily volume = rain * area * coeff?
SELECT "Date", "Mean_Catchment_Rainfall_mm" p, "Runoff_Coefficient_dimensionless" c, "Daily_Surface_Runoff_Volume_m3_day" q, round(("Mean_Catchment_Rainfall_mm"/1000*6.5e9*"Runoff_Coefficient_dimensionless")::numeric) p_area_c, "Peak_Flow_Rates_m3s" peak, round(("Daily_Surface_Runoff_Volume_m3_day"/86400)::numeric,2) mean_flow_m3s, "Baseflow_Component_m3s" bf FROM runoff_variables_master WHERE "District_Name"='Alfred Nzo' AND "Date" BETWEEN '2024-01-01' AND '2024-01-05' ORDER BY 1
---
4_5 irrigation vs 4_4 (same magnitude issue?) and CWR vs ET0 ratio
SELECT round(avg(a."CWR_mm_day"/nullif(b."Daily_ET0_mm",0))::numeric,3) cwr_over_et0 FROM "4_5_irrigation_demand_daily_master" a JOIN "4_4_potential_evaporation_daily_master" b USING ("District_Name","Date") WHERE a."Date" LIKE '2024-%'
---
met et0 vs 4_4 ET0 ratio distribution
SELECT round(min(m.et0_mm_day/nullif(e."Daily_ET0_mm",0))::numeric,2) min_ratio, round(avg(m.et0_mm_day/nullif(e."Daily_ET0_mm",0))::numeric,2) avg_ratio, round(max(m.et0_mm_day/nullif(e."Daily_ET0_mm",0))::numeric,2) max_ratio FROM meteorological_drought_master m JOIN "4_4_potential_evaporation_daily_master" e ON e."District_Name"=m.district_name AND e."Date"=m.date::text WHERE m.year=2024
