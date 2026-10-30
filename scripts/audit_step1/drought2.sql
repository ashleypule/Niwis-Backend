-- J2. per-district monthly runoff depth (mm) for CPT, DC34, DC44 2000-01..2026-08
WITH {W} SELECT w.code, string_agg(to_char(mth,'YYYY-MM')||':'||round(depth::numeric,4), ',' ORDER BY mth) FROM (SELECT w.code, date_trunc('month', "Date")::date mth, sum("Daily_Surface_Runoff_Volume_m3_day")/6.5e9*1000 depth FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2000-01-01' AND '2026-08-31' AND w.code IN ('CPT','DC34','DC44') GROUP BY 1,2) x JOIN w ON w.code = x.code GROUP BY 1;;
-- J3. per-district monthly mean flow (9_1 daily_mean_flow m3/day -> m3/s) for every district 2000-01..2026-08, with day counts
WITH {W} SELECT code, string_agg(to_char(mth,'YYYY-MM')||':'||round(q::numeric,4)||'/'||n, ',' ORDER BY mth) FROM (SELECT w.code, date_trunc('month', date)::date mth, avg(daily_mean_flow)/86400 q, count(daily_mean_flow) n FROM "9_1_hydrological_station_master" t JOIN w ON w.name=t.district_name WHERE date BETWEEN '2000-01-01' AND '2026-08-31' GROUP BY 1,2) x GROUP BY 1 ORDER BY 1;;
-- J4. 9_1 spellings not matched by the name table
SELECT DISTINCT district_name FROM "9_1_hydrological_station_master" WHERE district_name NOT IN (SELECT name FROM (VALUES ('x')) v(name)) ORDER BY 1;;
-- G1. groundwater depth anomaly Sep 2026: per-district monthly mean of samples; climatology = mean over 2000-2025 of the September monthly means; anomaly = clim - cur (+ = shallower); area-weighted
WITH {W}, m AS (SELECT w.code, w.area, date_trunc('month', date)::date mth, avg(value) v, count(*) n FROM "10_1_borehole_and_aquifer_master" t JOIN w ON w.name=t.district_name WHERE variable_name='groundwater_table_depth_m_bgl' AND date <= CURRENT_DATE GROUP BY 1,2,3),
cur AS (SELECT code, area, v FROM m WHERE mth='2026-09-01'),
cl AS (SELECT code, avg(v) n, count(*) yrs FROM m WHERE extract(month from mth)=9 AND extract(year from mth) BETWEEN 2000 AND 2025 GROUP BY 1)
SELECT round((sum(cur.area*(cl.n-cur.v))/sum(cur.area))::numeric,3) anomaly_m, count(*) n_districts, round((sum(cur.area)/1222435.4*100)::numeric,1) coverage_pct, min(yrs) min_yrs,
 (SELECT string_agg(code||':'||round((cl.n-cur.v)::numeric,2), ' ' ORDER BY code) FROM cur JOIN cl USING (code) WHERE code IN ('CPT','DC34','DC44')) d FROM cur JOIN cl USING (code);;
-- G2. which months exist in 10_1 depth telemetry in 2026 (sampling cadence) and the newest sample
SELECT date_trunc('month', date)::date mth, count(DISTINCT district_name) n_d, count(*) n, max(date) FROM "10_1_borehole_and_aquifer_master" WHERE variable_name='groundwater_table_depth_m_bgl' AND date >= '2026-01-01' AND date <= CURRENT_DATE GROUP BY 1 ORDER BY 1;;
-- R. SPI-24 DC44 Aug 2026 raw
SELECT "SPI_24_Very_Long_Term_Drought", "SPI_6_Medium_Term_Hydrological_Impacts" FROM "6.2_rainfall_derived_drought_indices_master" WHERE "District_Name"='Alfred Nzo' AND "Year"=2026 AND "Month"=8;;
