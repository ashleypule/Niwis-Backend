-- Which tables duplicate which (read-only). Feeds the canonical-source choices in app/registry/datasets.yaml.
-- Each block: rows compared and rows that differ by more than 1e-6. "differ = 0" means an exact duplicate.
daily rainfall: 6.1 Daily_Rainfall_Total_mm vs meteorological daily_rainfall_mm_day / 6.1 gridded 1 km / runoff Mean_Catchment_Rainfall_mm / 6.1 CHIRPS
SELECT count(*) n,
 count(*) FILTER (WHERE abs(r."Daily_Rainfall_Total_mm" - m.daily_rainfall_mm_day) > 1e-6) vs_met,
 count(*) FILTER (WHERE abs(r."Daily_Rainfall_Total_mm" - r."Gridded_Rainfall_Product_1km_mm") > 1e-6) vs_gridded,
 count(*) FILTER (WHERE abs(r."Daily_Rainfall_Total_mm" - u."Mean_Catchment_Rainfall_mm") > 1e-6) vs_runoff_rain,
 count(*) FILTER (WHERE abs(r."Daily_Rainfall_Total_mm" - r."CHIRPS_Daily_Rainfall_Estimate_mm_day") > 1e-6) vs_chirps,
 round(max(abs(r."Daily_Rainfall_Total_mm" - r."CHIRPS_Daily_Rainfall_Estimate_mm_day"))::numeric,2) chirps_max_diff,
 count(*) FILTER (WHERE abs(r."Hourly_Rainfall_Intensity_mm_h" - m.rainfall_intensity_mm_h) > 1e-6) intensity_vs_met
FROM "6.1_core_rainfall_variables_master" r
JOIN meteorological_drought_master m ON m.district_name = r."District_Name" AND m.date::text = r."Date"
JOIN runoff_variables_master u ON u."District_Name" = r."District_Name" AND u."Date"::text = r."Date"
---
monthly rainfall & anomalies: 6.1 vs meteorological (one row per district-month)
WITH r AS (SELECT DISTINCT ON ("District_Name","Year","Month") "District_Name" d, "Year" y, "Month" mo, "Monthly_Total_Precipitation_mm" p, "Monthly_Anomaly_mm" a, "Monthly_Anomaly_percent" ap FROM "6.1_core_rainfall_variables_master" ORDER BY 1,2,3),
     m AS (SELECT DISTINCT ON (district_name, year, month) district_name d, year y, month mo, monthly_rainfall_mm_month p, rainfall_anomaly_mm a, rainfall_anomaly_pct ap FROM meteorological_drought_master ORDER BY 1,2,3)
SELECT count(*) n, count(*) FILTER (WHERE abs(r.p-m.p)>1e-6) p_differ, count(*) FILTER (WHERE abs(r.p-m.p)>1e-6 AND r.mo<>12) p_differ_not_dec, count(*) FILTER (WHERE abs(r.a-m.a)>1e-6) anom_differ, count(*) FILTER (WHERE abs(r.ap-m.ap)>1e-6) anom_pct_differ FROM r JOIN m USING (d, y, mo)
---
annual rainfall: 6.1 Annual_Total vs 4_3 MAP_mm vs 4_2 PRCPTOT (full years 2000-2025)
WITH r AS (SELECT DISTINCT ON ("District_Name","Year") "District_Name" d, "Year" y, "Annual_Total_Precipitation_mm" p FROM "6.1_core_rainfall_variables_master" ORDER BY 1,2)
SELECT count(*) n, count(*) FILTER (WHERE abs(r.p - a."MAP_mm")>1e-6) vs_4_3_map, round(max(abs(r.p - a."MAP_mm"))::numeric,2) max_diff_map, count(*) FILTER (WHERE abs(r.p - b."PRCPTOT_mm")>1e-6) vs_4_2_prcptot
FROM r JOIN "4_3_mean_annual_precipitation_master" a ON a."District_Name"=r.d AND a."Year"=r.y JOIN "4_2_wet_dry_spells_master" b ON b."District_Name"=r.d AND b."Year"=r.y WHERE r.y <= 2025
---
SPI: 6.2 vs meteorological vs 5.2 (all scales)
WITH m AS (SELECT DISTINCT ON (district_name, year, month) district_name d, year y, month mo, spi_1, spi_3, spi_6, spi_12, spi_24, spei_1, spei_3, spei_6, spei_12 FROM meteorological_drought_master ORDER BY 1,2,3)
SELECT count(*) n,
 count(*) FILTER (WHERE abs(s."SPI_1_Meteorological_Drought_Onset"-m.spi_1)>1e-6 OR abs(s."SPI_3_Short_Term_Agricultural_Drought"-m.spi_3)>1e-6 OR abs(s."SPI_6_Medium_Term_Hydrological_Impacts"-m.spi_6)>1e-6 OR abs(s."SPI_12_Long_Term_Groundwater_Impacts"-m.spi_12)>1e-6 OR abs(s."SPI_24_Very_Long_Term_Drought"-m.spi_24)>1e-6) spi_6_2_vs_met,
 count(*) FILTER (WHERE abs(s."SPEI_3_Agricultural_Drought_ET"-m.spei_3)>1e-6) spei3_6_2_vs_met,
 count(*) FILTER (WHERE abs(s."SPEI_12_Annual_Water_Balance_Drought"-m.spei_12)>1e-6) spei12_6_2_vs_met,
 count(*) FILTER (WHERE abs(f."SPI_1_month"-m.spi_1)>1e-6 OR abs(f."SPI_3_month"-m.spi_3)>1e-6 OR abs(f."SPI_6_month"-m.spi_6)>1e-6) spi_5_2_vs_met,
 count(*) FILTER (WHERE abs(f."SPEI_1_month"-m.spei_1)>1e-6 OR abs(f."SPEI_3_month"-m.spei_3)>1e-6 OR abs(f."SPEI_6_month"-m.spei_6)>1e-6) spei_5_2_vs_met,
 round(max(abs(f."SPEI_6_month"-m.spei_6))::numeric,3) spei6_5_2_max_diff
FROM m JOIN "6.2_rainfall_derived_drought_indices_master" s ON s."District_Name"=m.d AND s."Year"=m.y AND s."Month"=m.mo
JOIN "5.2_meteorological_drivers_saws_master" f ON f."District_Name"=m.d AND f."Year"=m.y AND f."Month"=m.mo
---
temperature: 4_1 vs meteorological (Tmin/Tmax/Tavg/anomaly) and 4_5 Growing_Season_Temp_C vs Tavg
SELECT count(*) n,
 count(*) FILTER (WHERE abs(t."Tmin_C"-m.tmin_c)>1e-6 OR abs(t."Tmax_C"-m.tmax_c)>1e-6) tmin_tmax_differ,
 count(*) FILTER (WHERE abs(t."Tavg_C"-m.tavg_c)>1e-6) tavg_differ,
 round(max(abs(t."Tavg_C"-m.tavg_c))::numeric,4) tavg_max_diff,
 count(*) FILTER (WHERE abs(t."Temperature_Anomaly_C"-m.temp_anomaly_c)>1e-6) anom_differ,
 count(*) FILTER (WHERE abs(g."Growing_Season_Temp_C"-m.tavg_c)>1e-6) gst_vs_tavg
FROM "4_1_temperature_change_master" t JOIN meteorological_drought_master m ON m.district_name=t."District_Name" AND m.date::text=t."Date"
JOIN "4_5_irrigation_demand_daily_master" g ON g."District_Name"=t."District_Name" AND g."Date"=t."Date"
---
monthly ET0 and mean temperature: 5.2 vs sums/means of meteorological daily
WITH m AS (SELECT district_name d, year y, month mo, sum(et0_mm_day) et0, avg(tavg_c) t, count(*) days FROM meteorological_drought_master GROUP BY 1,2,3)
SELECT count(*) n, count(*) FILTER (WHERE abs(f."Potential_Evapotranspiration_ET0_mm"-m.et0)>0.01) et0_differ, round(max(abs(f."Potential_Evapotranspiration_ET0_mm"-m.et0))::numeric,2) et0_max_diff, round(avg(f."Potential_Evapotranspiration_ET0_mm"/nullif(m.et0,0))::numeric,3) et0_ratio, count(*) FILTER (WHERE abs(f."Mean_Temperature_C"-m.t)>0.01) temp_differ, round(max(abs(f."Mean_Temperature_C"-m.t))::numeric,3) temp_max_diff
FROM m JOIN "5.2_meteorological_drivers_saws_master" f ON f."District_Name"=m.d AND f."Year"=m.y AND f."Month"=m.mo
---
LST: satellite (monthly) vs 4_1 LST_Day_C (daily, monthly mean) vs 5.1 LST
WITH a AS (SELECT "District_Name" d, make_date(substr("Date",1,4)::int, substr("Date",6,2)::int, 1) dt, avg("LST_Day_C") lst_day FROM "4_1_temperature_change_master" GROUP BY 1,2),
     b AS (SELECT "District_Name" d, make_date("Year"::int,"Month"::int,1) dt, avg("LST_Land_Surface_Temperature_C") lst51 FROM "5.1_primary_satellite_vegetation_variables_sansa_master" GROUP BY 1,2)
SELECT round(corr(s.lst, a.lst_day)::numeric,2) sat_vs_4_1_day_r, round(avg(s.lst - a.lst_day)::numeric,2) sat_minus_4_1_day, round(corr(s.lst, b.lst51)::numeric,2) sat_vs_5_1_r, round(corr(a.lst_day, b.lst51)::numeric,2) r_4_1_vs_5_1, count(*) n
FROM satellite_remote_sensing s JOIN a ON lower(a.d)=lower(s.district_name) AND a.dt=s.date JOIN b ON b.d=a.d AND b.dt=a.dt
---
5.1 other indices follow 5.1 NDVI (same failed source)?
WITH b AS (SELECT "District_Name" d, "Month" mo, avg("NDVI_10day_composite") ndvi, avg("EVI") evi, avg("SAVI") savi, avg("NDII") ndii, avg("VCI_percent") vci FROM "5.1_primary_satellite_vegetation_variables_sansa_master" GROUP BY 1,2)
SELECT round(corr(ndvi, evi)::numeric,3) ndvi_evi, round(corr(ndvi, savi)::numeric,3) ndvi_savi, round(corr(ndvi, ndii)::numeric,3) ndvi_ndii, round(stddev(ndvi)::numeric,3) sd_ndvi_clim FROM b
---
runoff vs hydrology vs 4_6 (different models?)
SELECT round(corr(u.q, h.surface_runoff_m3_s)::numeric,2) runoff_vs_hydro_r FROM (SELECT "District_Name" d, make_date("Year","Month",1) dt, sum("Daily_Surface_Runoff_Volume_m3_day") q FROM runoff_variables_master GROUP BY 1,2) u JOIN hydrological_drought_master h ON lower(h.district_name)=lower(u.d) AND h.date=u.dt
