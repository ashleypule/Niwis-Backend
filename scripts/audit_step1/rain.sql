-- A. summary national: total / anomaly / normal / r20 (window 2025-09-07..2026-09-06), days present
WITH {W},
d AS (SELECT w.code, w.area, sum("Daily_Rainfall_Total_mm") s, count("Daily_Rainfall_Total_mm") n, max("Daily_Rainfall_Total_mm") mx,
             count(*) FILTER (WHERE "Daily_Rainfall_Total_mm" >= 20) r20
      FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name = t."District_Name"
      WHERE "Date" BETWEEN '2025-09-07' AND '2026-09-06' GROUP BY 1,2),
nm AS (SELECT w.code, "Month" mo, max("Monthly_Total_Precipitation_mm" - "Monthly_Anomaly_mm") n, count(DISTINCT round(("Monthly_Total_Precipitation_mm" - "Monthly_Anomaly_mm")::numeric, 3)) nd
       FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Date" <= '2026-10-30' GROUP BY 1,2),
days AS (SELECT d::date d FROM generate_series('2025-09-07'::date,'2026-09-06'::date, interval '1 day') d),
frac AS (SELECT extract(month from d) mo, sum(1.0/extract(day from (date_trunc('month', d) + interval '1 month - 1 day'))) f FROM days GROUP BY 1),
nw AS (SELECT nm.code, sum(nm.n * frac.f) n FROM nm JOIN frac ON frac.mo = nm.mo GROUP BY 1)
SELECT round((sum(d.area*d.s)/sum(d.area))::numeric,1) total, round(((sum(d.area*d.s)-sum(d.area*nw.n))/sum(d.area*nw.n)*100)::numeric,1) anomaly,
       round((sum(d.area*nw.n)/sum(d.area))::numeric,0) normal, round((sum(d.area*d.r20)/sum(d.area))::numeric,1) r20, max(d.mx) maxdaily, count(*) n_districts, min(d.n) min_days, max(d.n) max_days,
       (SELECT max(nd) FROM nm) normal_distinct_per_district_month
FROM d JOIN nw USING (code);;
-- A2. per-district window stats for CPT, DC34, DC44, DC33, DC6 (+ anomaly) and previous-year total (delta)
WITH {W},
d AS (SELECT w.code, sum("Daily_Rainfall_Total_mm") s, count(*) n, max("Daily_Rainfall_Total_mm") mx, (array_agg("Date" ORDER BY "Daily_Rainfall_Total_mm" DESC))[1] mxd,
             count(*) FILTER (WHERE "Daily_Rainfall_Total_mm" >= 20) r20
      FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name = t."District_Name"
      WHERE "Date" BETWEEN '2025-09-07' AND '2026-09-06' GROUP BY 1),
p AS (SELECT w.code, sum("Daily_Rainfall_Total_mm") s FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name = t."District_Name"
      WHERE "Date" BETWEEN '2024-09-07' AND '2025-09-06' GROUP BY 1),
nm AS (SELECT w.code, "Month" mo, max("Monthly_Total_Precipitation_mm" - "Monthly_Anomaly_mm") n FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" GROUP BY 1,2),
days AS (SELECT d::date d FROM generate_series('2025-09-07'::date,'2026-09-06'::date, interval '1 day') d),
frac AS (SELECT extract(month from d) mo, sum(1.0/extract(day from (date_trunc('month', d) + interval '1 month - 1 day'))) f FROM days GROUP BY 1),
nw AS (SELECT nm.code, sum(nm.n * frac.f) n FROM nm JOIN frac ON frac.mo = nm.mo GROUP BY 1)
SELECT d.code, round(d.s::numeric,1) total, round((d.s - p.s)::numeric,1) delta, round(((d.s-nw.n)/nw.n*100)::numeric,1) anomaly, round(nw.n::numeric,0) normal, d.r20, round(d.mx::numeric,1) mx, d.mxd, d.n
FROM d JOIN nw USING (code) JOIN p USING (code) WHERE d.code IN ('CPT','DC34','DC44','DC33','DC6') ORDER BY 1;;
-- A3. national delta: previous window total
WITH {W}, p AS (SELECT w.code, w.area, sum("Daily_Rainfall_Total_mm") s FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name = t."District_Name"
      WHERE "Date" BETWEEN '2024-09-07' AND '2025-09-06' GROUP BY 1,2)
SELECT round((sum(area*s)/sum(area))::numeric,1) prev_total FROM p;;
-- C. CDD current spell: days since last day >= 1 mm, per district (area-weighted national)
WITH {W}, l AS (SELECT w.code, w.area, max("Date"::date) FILTER (WHERE "Daily_Rainfall_Total_mm" >= 1) lastwet FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2024-09-07' AND '2026-09-06' GROUP BY 1,2)
SELECT round((sum(area*(DATE '2026-09-06' - lastwet))/sum(area))::numeric,2) cdd_national, round((sum(area*(DATE '2026-09-06' - lastwet))/sum(area))::numeric,0) rounded,
 (SELECT string_agg(code || '=' || (DATE '2026-09-06' - lastwet), ',' ORDER BY code) FROM l WHERE code IN ('CPT','DC10','DC44','DC34','DC2','DC8')) samples FROM l;;
-- D. SPI-6 / SPEI-6 national Aug 2026 (area-weighted) and district values
WITH {W}, s AS (SELECT w.code, w.area, "SPI_6_Medium_Term_Hydrological_Impacts" v6, "SPI_1_Meteorological_Drought_Onset" v1, "SPI_3_Short_Term_Agricultural_Drought" v3, "SPI_12_Long_Term_Groundwater_Impacts" v12, "SPEI_12_Annual_Water_Balance_Drought" spei12_62 FROM "6.2_rainfall_derived_drought_indices_master" t JOIN w ON w.name=t."District_Name" WHERE "Year"=2026 AND "Month"=8),
e AS (SELECT w.code, max(spei_6) spei6, max(spei_12) spei12, count(DISTINCT spei_6) nd FROM meteorological_drought_master t JOIN w ON w.name=t.district_name WHERE date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY 1)
SELECT round((sum(area*v6)/sum(area))::numeric,2) spi6, round((sum(area*v1)/sum(area))::numeric,2) spi1, round((sum(area*v3)/sum(area))::numeric,2) spi3, round((sum(area*v12)/sum(area))::numeric,2) spi12,
       round((sum(area*spei6)/sum(area))::numeric,2) spei6, round((sum(area*spei12)/sum(area))::numeric,2) spei12, count(*) n, max(e.nd) spei_distinct_in_month,
       (SELECT string_agg(code || ':' || round(v6::numeric,2) || '/' || round(v3::numeric,2) || '/' || round(v12::numeric,2) || '/' || round(v1::numeric,2), ' ' ORDER BY code) FROM s WHERE code IN ('CPT','DC34','DC44','DC23')) spi_6_3_12_1,
       (SELECT string_agg(code || ':' || round(spei6::numeric,2), ' ' ORDER BY code) FROM e WHERE code IN ('CPT','DC34','DC44','DC23')) spei6_d
FROM s JOIN e USING (code);;
-- E. monthly chart: area-weighted monthly sums 2024-2026
WITH {W}, m AS (SELECT w.code, w.area, "Year" y, "Month" mo, sum("Daily_Rainfall_Total_mm") s FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" >= 2024 AND "Date" <= '2026-09-06' GROUP BY 1,2,3,4)
SELECT y, string_agg(mo || ':' || v, ' ' ORDER BY mo) FROM (SELECT y, mo, round((sum(area*s)/sum(area))::numeric,1) v FROM m GROUP BY y, mo) x GROUP BY y ORDER BY y;;
-- E2. normals per calendar month (area-weighted)
WITH {W}, nm AS (SELECT w.code, w.area, "Month" mo, max("Monthly_Total_Precipitation_mm" - "Monthly_Anomaly_mm") n FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" GROUP BY 1,2,3)
SELECT string_agg(mo || ':' || n, ' ' ORDER BY mo) normals FROM (SELECT mo, round((sum(area*n)/sum(area))::numeric,1) n FROM nm GROUP BY mo) x;;
-- F. anomaly series windows 1/3/12 for 2025-09..2026-08 (national)
WITH {W}, m AS (SELECT w.code, w.area, make_date("Year"::int,"Month"::int,1) mth, sum("Daily_Rainfall_Total_mm") s FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2024-09-01' AND '2026-08-31' GROUP BY 1,2,3),
nm AS (SELECT w.code, w.area, "Month" mo, max("Monthly_Total_Precipitation_mm" - "Monthly_Anomaly_mm") n FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" GROUP BY 1,2,3),
agg AS (SELECT mth, sum(area*s)/sum(area) p FROM m GROUP BY 1),
nagg AS (SELECT mo, sum(area*n)/sum(area) n FROM nm GROUP BY 1),
j AS (SELECT mth, p, n FROM agg JOIN nagg ON nagg.mo = extract(month from mth)),
r AS (SELECT mth, p, n, sum(p) OVER (ORDER BY mth ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) p3, sum(n) OVER (ORDER BY mth ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) n3,
             sum(p) OVER (ORDER BY mth ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) p12, sum(n) OVER (ORDER BY mth ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) n12 FROM j)
SELECT to_char(mth,'YYYY-MM') m, round(((p-n)/n*100)::numeric,1) a1, round(((p3-n3)/n3*100)::numeric,1) a3, round(((p12-n12)/n12*100)::numeric,1) a12 FROM r WHERE mth >= '2025-09-01' ORDER BY 1;;
-- H. intensity Aug 2026: max daily any district; r20 area-weighted
WITH {W}, m AS (SELECT w.code, w.area, max("Daily_Rainfall_Total_mm") mx, count(*) FILTER (WHERE "Daily_Rainfall_Total_mm" >= 20) r20 FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY 1,2)
SELECT max(mx) maxdaily, round((sum(area*r20)/sum(area))::numeric,2) r20 FROM m;;
-- I. dry-wet 2025 / 2000 (4_2), area-weighted
WITH {W} SELECT "Year", round((sum(area*"CDD_Days")/sum(area))::numeric,1) cdd, round((sum(area*"CWD_Days")/sum(area))::numeric,1) cwd, round((sum(area*"R20mm_Days")/sum(area))::numeric,1) r20, round((sum(area*"Rx1day_mm")/sum(area))::numeric,1) rx1, count(*) n FROM "4_2_wet_dry_spells_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" IN (2000, 2024, 2025) GROUP BY 1 ORDER BY 1;;
-- J. station DC44 Aug 2026
SELECT round(sum("Daily_Rainfall_Total_mm")::numeric,1) rain FROM "6.1_core_rainfall_variables_master" WHERE "District_Name"='Alfred Nzo' AND "Date" BETWEEN '2026-08-01' AND '2026-08-31';;
-- K. seasonal 2025 national (stored seasonal columns, area-weighted) + check stored JFM = sum of Jan-Mar daily
WITH {W}, s AS (SELECT w.code, w.area, max("Seasonal_Total_JFM_mm") jfm, max("Seasonal_Total_AMJ_mm") amj, max("Seasonal_Total_JAS_mm") jas, max(NULLIF("Seasonal_Total_OND_mm",'')::double precision) ond, count(DISTINCT "Seasonal_Total_JFM_mm") ndj,
  sum("Daily_Rainfall_Total_mm") FILTER (WHERE "Month" IN (1,2,3)) jfm_daily, sum("Daily_Rainfall_Total_mm") FILTER (WHERE "Month" IN (10,11,12)) ond_daily
  FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Year"=2025 GROUP BY 1,2)
SELECT round((sum(area*jfm)/sum(area))::numeric,1) jfm, round((sum(area*amj)/sum(area))::numeric,1) amj, round((sum(area*jas)/sum(area))::numeric,1) jas, round((sum(area*ond)/sum(area))::numeric,1) ond, max(ndj) jfm_distinct,
  round(max(abs(jfm - jfm_daily))::numeric,2) max_abs_jfm_diff, round(max(abs(ond - ond_daily))::numeric,2) max_abs_ond_diff FROM s;;
-- L. events > 50 mm district-days per year (national), highest daily per year
SELECT "Year", count(*) FILTER (WHERE "Daily_Rainfall_Total_mm" > 50) ev, count(*) FILTER (WHERE "Daily_Rainfall_Total_mm" >= 50.01) ev2, round(max("Daily_Rainfall_Total_mm")::numeric,1) mx FROM "6.1_core_rainfall_variables_master" WHERE "Year" >= 2017 AND "Date" <= '2026-09-06' GROUP BY 1 ORDER BY 1;;
-- M. provinces table
WITH {W},
d AS (SELECT w.code, w.prov, w.area, sum("Daily_Rainfall_Total_mm") s FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name = t."District_Name" WHERE "Date" BETWEEN '2025-09-07' AND '2026-09-06' GROUP BY 1,2,3),
nm AS (SELECT w.code, "Month" mo, max("Monthly_Total_Precipitation_mm" - "Monthly_Anomaly_mm") n FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" GROUP BY 1,2),
days AS (SELECT d::date d FROM generate_series('2025-09-07'::date,'2026-09-06'::date, interval '1 day') d),
frac AS (SELECT extract(month from d) mo, sum(1.0/extract(day from (date_trunc('month', d) + interval '1 month - 1 day'))) f FROM days GROUP BY 1),
nw AS (SELECT nm.code, sum(nm.n * frac.f) n FROM nm JOIN frac ON frac.mo = nm.mo GROUP BY 1),
s12 AS (SELECT w.code, "SPI_12_Long_Term_Groundwater_Impacts" v FROM "6.2_rainfall_derived_drought_indices_master" t JOIN w ON w.name=t."District_Name" WHERE "Year"=2026 AND "Month"=8)
SELECT d.prov, round((sum(d.area*d.s)/sum(d.area))::numeric,1) total, round(((sum(d.area*d.s)-sum(d.area*nw.n))/sum(d.area*nw.n)*100)::numeric,1) anomaly, round((sum(d.area*s12.v)/sum(d.area))::numeric,2) spi12
FROM d JOIN nw USING (code) JOIN s12 USING (code) GROUP BY 1 ORDER BY 2 DESC;;
-- O. statistics: annual rainfall (4_3 MAP_mm) 2025 and 2000-2025 mean, Sen's slope x10
WITH {W}, y AS (SELECT "Year" yr, sum(area*"MAP_mm")/sum(area) v FROM "4_3_mean_annual_precipitation_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" BETWEEN 2000 AND 2025 GROUP BY 1)
SELECT (SELECT round(v::numeric,0) FROM y WHERE yr=2025) v2025, round(avg(v)::numeric,0) clim, (SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.v-a.v)/(b.yr-a.yr)) * 10)::numeric,2) FROM y a JOIN y b ON b.yr > a.yr) sen10 FROM y;;
-- O2. rainy days >= 1 mm per year, area-weighted
WITH {W}, y AS (SELECT w.code, w.area, "Year" yr, count(*) FILTER (WHERE "Daily_Rainfall_Total_mm" >= 1) rd FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" BETWEEN 2000 AND 2025 GROUP BY 1,2,3),
a AS (SELECT yr, sum(area*rd)/sum(area) v FROM y GROUP BY 1)
SELECT (SELECT round(v::numeric,0) FROM a WHERE yr=2025) rainy2025, round(avg(v)::numeric,0) clim, (SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.v-a.v)/(b.yr-a.yr)) * 10)::numeric,2) FROM a a JOIN a b ON b.yr > a.yr) sen10 FROM a;;
-- O3. r20 / rx1 / cdd 2025, climatology, Sen's slopes
WITH {W}, y AS (SELECT "Year" yr, sum(area*"R20mm_Days")/sum(area) r20, sum(area*"Rx1day_mm")/sum(area) rx1, sum(area*"CDD_Days")/sum(area) cdd FROM "4_2_wet_dry_spells_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" BETWEEN 2000 AND 2025 GROUP BY 1)
SELECT (SELECT round(r20::numeric,1)||'/'||round(rx1::numeric,1)||'/'||round(cdd::numeric,0) FROM y WHERE yr=2025) v2025, round(avg(r20)::numeric,1)||'/'||round(avg(rx1)::numeric,1)||'/'||round(avg(cdd)::numeric,0) clim,
 (SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.r20-a.r20)/(b.yr-a.yr)) * 10)::numeric,2) FROM y a JOIN y b ON b.yr > a.yr) sen_r20,
 (SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.rx1-a.rx1)/(b.yr-a.yr)) * 10)::numeric,2) FROM y a JOIN y b ON b.yr > a.yr) sen_rx1,
 (SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.cdd-a.cdd)/(b.yr-a.yr)) * 10)::numeric,2) FROM y a JOIN y b ON b.yr > a.yr) sen_cdd FROM y;;
-- P. maps: monthly Sep 2026 partial; MAP 2000-2025 mean; r20 2025
WITH {W} SELECT w.code, round(sum("Daily_Rainfall_Total_mm")::numeric,1) sep26 FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2026-09-01' AND '2026-09-06' AND w.code IN ('DC42','CPT','DC34','DC44') GROUP BY 1 ORDER BY 1;;
WITH {W} SELECT w.code, round(avg("MAP_mm")::numeric,0) map, count(*) n FROM "4_3_mean_annual_precipitation_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" BETWEEN 2000 AND 2025 AND w.code IN ('BUF','CPT','DC34','DC44') GROUP BY 1 ORDER BY 1;;
WITH {W} SELECT w.code, "R20mm_Days" FROM "4_2_wet_dry_spells_master" t JOIN w ON w.name=t."District_Name" WHERE "Year"=2025 AND w.code IN ('DC25','CPT','DC34','DC44') ORDER BY 1;;
-- Q. newest dates
SELECT (SELECT max("Date") FROM "6.1_core_rainfall_variables_master") d61, (SELECT max(make_date("Year"::int,"Month"::int,1)) FROM "6.2_rainfall_derived_drought_indices_master") d62, (SELECT max(date) FROM meteorological_drought_master) dmet, (SELECT max("Year") FROM "4_2_wet_dry_spells_master") y42, (SELECT max("Year") FROM "4_3_mean_annual_precipitation_master") y43;;
