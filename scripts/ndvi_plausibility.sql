-- D4 evidence: plausibility of the two NDVI sources (read-only). Run: python scripts/run_checks.py scripts/ndvi_plausibility.sql
-- satellite_remote_sensing: monthly rows, lowercase names (5 aliases).  5.1: daily rows, monthly mean taken here.
-- Common period 2001-01 .. 2025-12 so both sources are compared over the same months.
arid west: annual-mean NDVI and monthly range, 2001-2025 (expect ~0.10-0.25)
WITH s AS (SELECT lower(district_name) d, extract(month FROM date)::int m, ndvi v FROM satellite_remote_sensing),
     f AS (SELECT lower("District_Name") d, "Month"::int m, avg("NDVI_10day_composite") v FROM "5.1_primary_satellite_vegetation_variables_sansa_master" WHERE "Year" BETWEEN 2001 AND 2025 GROUP BY 1, "Year", 2)
SELECT 'satellite' src, d, round(avg(v)::numeric,3) mean, round(min(v)::numeric,3) min, round(max(v)::numeric,3) max FROM s WHERE d IN ('namakwa','z f mgcawu','pixley ka seme','central karoo','john taolo gaetsewe') GROUP BY d
UNION ALL
SELECT '5.1', d, round(avg(v)::numeric,3), round(min(v)::numeric,3), round(max(v)::numeric,3) FROM f WHERE d IN ('namakwa','z f mgcawu','pixley ka seme','central karoo','john taolo gaetsewe') GROUP BY d ORDER BY 2, 1
---
wet east: mean NDVI in wet season (DJF) vs dry season (JJA), 2001-2025 (expect high DJF, e.g. > 0.5)
WITH s AS (SELECT lower(district_name) d, extract(month FROM date)::int m, ndvi v FROM satellite_remote_sensing),
     f AS (SELECT lower("District_Name") d, "Month"::int m, avg("NDVI_10day_composite") v FROM "5.1_primary_satellite_vegetation_variables_sansa_master" WHERE "Year" BETWEEN 2001 AND 2025 GROUP BY 1, "Year", 2),
     u AS (SELECT 'satellite' src, * FROM s UNION ALL SELECT '5.1', * FROM f)
SELECT src, d, round(avg(v) FILTER (WHERE m IN (12,1,2))::numeric,3) djf, round(avg(v) FILTER (WHERE m IN (6,7,8))::numeric,3) jja
FROM u WHERE d IN ('ethekwini','ugu','ehlanzeni','vhembe','umgungundlovu','o.r.tambo','ortambo','alfred nzo') GROUP BY 1,2 ORDER BY 2,1
---
seasonal cycle: month of peak NDVI climatology vs month of peak rainfall climatology, per district and source
WITH r AS (SELECT lower("District_Name") d, "Month"::int m, avg(p) rain FROM (SELECT "District_Name","Year","Month", max("Monthly_Total_Precipitation_mm") p FROM "6.1_core_rainfall_variables_master" WHERE "Year" BETWEEN 2001 AND 2025 GROUP BY 1,2,3) x GROUP BY 1,2),
     s AS (SELECT lower(district_name) d, extract(month FROM date)::int m, avg(ndvi) v FROM satellite_remote_sensing GROUP BY 1,2),
     f AS (SELECT lower("District_Name") d, "Month"::int m, avg("NDVI_10day_composite") v FROM "5.1_primary_satellite_vegetation_variables_sansa_master" WHERE "Year" BETWEEN 2001 AND 2025 GROUP BY 1,2),
     alias AS (SELECT * FROM (VALUES ('cape town','city of cape town'),('johannesburg','city of johannesburg'),('tshwane','city of tshwane'),('ortambo','o.r.tambo'),('sarah baartman','cacadu')) a(sat, std)),
     s2 AS (SELECT coalesce(a.std, s.d) d, s.m, s.v FROM s LEFT JOIN alias a ON a.sat = s.d),
     pk AS (
       SELECT d, (array_agg(m ORDER BY rain DESC))[1] rain_peak FROM r GROUP BY d),
     ps AS (SELECT d, (array_agg(m ORDER BY v DESC))[1] sat_peak, round((max(v)-min(v))::numeric,3) sat_amp FROM s2 GROUP BY d),
     pf AS (SELECT d, (array_agg(m ORDER BY v DESC))[1] f51_peak, round((max(v)-min(v))::numeric,3) f51_amp FROM f GROUP BY d),
     cs AS (SELECT r.d, round(corr(s2.v, r2.rain)::numeric,2) sat_corr_lag1, round(corr(f.v, r2.rain)::numeric,2) f51_corr_lag1
            FROM r JOIN r r2 ON r2.d = r.d AND r2.m = CASE WHEN r.m = 1 THEN 12 ELSE r.m - 1 END
            JOIN s2 ON s2.d = r.d AND s2.m = r.m JOIN f ON f.d = r.d AND f.m = r.m GROUP BY r.d)
SELECT pk.d, pk.rain_peak, ps.sat_peak, ps.sat_amp, cs.sat_corr_lag1, pf.f51_peak, pf.f51_amp, cs.f51_corr_lag1
FROM pk JOIN ps USING (d) JOIN pf USING (d) JOIN cs USING (d) ORDER BY pk.rain_peak, pk.d
---
summary: share of districts whose NDVI climatology correlates (r >= 0.5) with previous-month rainfall climatology
WITH r AS (SELECT lower("District_Name") d, "Month"::int m, avg(p) rain FROM (SELECT "District_Name","Year","Month", max("Monthly_Total_Precipitation_mm") p FROM "6.1_core_rainfall_variables_master" WHERE "Year" BETWEEN 2001 AND 2025 GROUP BY 1,2,3) x GROUP BY 1,2),
     s AS (SELECT lower(district_name) d, extract(month FROM date)::int m, avg(ndvi) v FROM satellite_remote_sensing GROUP BY 1,2),
     alias AS (SELECT * FROM (VALUES ('cape town','city of cape town'),('johannesburg','city of johannesburg'),('tshwane','city of tshwane'),('ortambo','o.r.tambo'),('sarah baartman','cacadu')) a(sat, std)),
     s2 AS (SELECT coalesce(a.std, s.d) d, s.m, s.v FROM s LEFT JOIN alias a ON a.sat = s.d),
     f AS (SELECT lower("District_Name") d, "Month"::int m, avg("NDVI_10day_composite") v FROM "5.1_primary_satellite_vegetation_variables_sansa_master" WHERE "Year" BETWEEN 2001 AND 2025 GROUP BY 1,2),
     c AS (SELECT r.d, corr(s2.v, r2.rain) cs, corr(f.v, r2.rain) cf FROM r JOIN r r2 ON r2.d = r.d AND r2.m = CASE WHEN r.m = 1 THEN 12 ELSE r.m - 1 END JOIN s2 ON s2.d = r.d AND s2.m = r.m JOIN f ON f.d = r.d AND f.m = r.m GROUP BY r.d)
SELECT count(*) districts, count(*) FILTER (WHERE cs >= 0.5) satellite_r_ge_05, count(*) FILTER (WHERE cf >= 0.5) f51_r_ge_05, round(avg(cs)::numeric,2) satellite_mean_r, round(avg(cf)::numeric,2) f51_mean_r FROM c
---
national ranking sanity: wettest vs driest district by mean NDVI (expect eastern districts top, NC/WC karoo bottom)
WITH s AS (SELECT lower(district_name) d, avg(ndvi) v FROM satellite_remote_sensing GROUP BY 1),
     f AS (SELECT lower("District_Name") d, avg("NDVI_10day_composite") v FROM "5.1_primary_satellite_vegetation_variables_sansa_master" WHERE "Year" BETWEEN 2001 AND 2025 GROUP BY 1)
SELECT 'satellite top' k, string_agg(d || ' ' || round(v::numeric,2), ', ') FROM (SELECT * FROM s ORDER BY v DESC LIMIT 5) x
UNION ALL SELECT 'satellite bottom', string_agg(d || ' ' || round(v::numeric,2), ', ') FROM (SELECT * FROM s ORDER BY v ASC LIMIT 5) x
UNION ALL SELECT '5.1 top', string_agg(d || ' ' || round(v::numeric,2), ', ') FROM (SELECT * FROM f ORDER BY v DESC LIMIT 5) x
UNION ALL SELECT '5.1 bottom', string_agg(d || ' ' || round(v::numeric,2), ', ') FROM (SELECT * FROM f ORDER BY v ASC LIMIT 5) x
---
NDVI vs that month's rainfall anomaly: does NDVI respond to wet/dry years? (corr of monthly NDVI anomaly with 3-month rainfall, per source)
WITH rain AS (SELECT lower("District_Name") d, make_date("Year"::int,"Month"::int,1) dt, max("Monthly_Total_Precipitation_mm") p FROM "6.1_core_rainfall_variables_master" WHERE "Year" BETWEEN 2001 AND 2025 GROUP BY 1,2),
     r3 AS (SELECT d, dt, sum(p) OVER (PARTITION BY d ORDER BY dt ROWS 2 PRECEDING) p3 FROM rain),
     r3a AS (SELECT d, dt, p3 - avg(p3) OVER (PARTITION BY d, extract(month FROM dt)) a FROM r3),
     alias AS (SELECT * FROM (VALUES ('cape town','city of cape town'),('johannesburg','city of johannesburg'),('tshwane','city of tshwane'),('ortambo','o.r.tambo'),('sarah baartman','cacadu')) a(sat, std)),
     s AS (SELECT coalesce(a.std, lower(district_name)) d, date dt, ndvi v FROM satellite_remote_sensing x LEFT JOIN alias a ON a.sat = lower(x.district_name)),
     sa AS (SELECT d, dt, v - avg(v) OVER (PARTITION BY d, extract(month FROM dt)) a FROM s),
     f AS (SELECT lower("District_Name") d, make_date("Year"::int,"Month"::int,1) dt, avg("NDVI_10day_composite") v FROM "5.1_primary_satellite_vegetation_variables_sansa_master" WHERE "Year" BETWEEN 2001 AND 2025 GROUP BY 1,2),
     fa AS (SELECT d, dt, v - avg(v) OVER (PARTITION BY d, extract(month FROM dt)) a FROM f)
SELECT round(corr(sa.a, r3a.a)::numeric,2) satellite_anom_vs_rain3, round(corr(fa.a, r3a.a)::numeric,2) f51_anom_vs_rain3, count(*) n
FROM r3a JOIN sa USING (d, dt) JOIN fa USING (d, dt)
