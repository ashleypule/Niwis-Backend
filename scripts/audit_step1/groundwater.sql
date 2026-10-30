-- A. 10_1 variables and the 10_2 / 11.x table names
SELECT variable_name, count(*) n, min(date), max(date) FROM "10_1_borehole_and_aquifer_master" WHERE date <= CURRENT_DATE GROUP BY 1 ORDER BY 1;;
SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND (table_name LIKE '10_2%' OR table_name LIKE '11%') ORDER BY 1;;
-- B. depth: district monthly means of samples; national area-weighted series (months with >= 95 % of area sampled): Sep 2026 level, Oct 2025 .. Sep 2026, same-month means 2000-2025, annual means, lt mean, Sen's slope
WITH {W}, wl AS (SELECT DISTINCT lower(name) lname, code, prov, area FROM w),
m AS (SELECT wl.code, wl.area, date_trunc('month', date)::date mth, avg(value) v FROM "10_1_borehole_and_aquifer_master" t JOIN wl ON wl.lname=lower(t.district_name) WHERE variable_name='groundwater_table_depth_m_bgl' AND date <= '2026-09-30' GROUP BY 1,2,3),
n AS (SELECT mth, sum(area*v)/sum(area) v, sum(area)/1222435.4 cov FROM m GROUP BY 1 HAVING sum(area)/1222435.4 >= 0.95),
lt AS (SELECT extract(month from mth) mo, avg(v) v FROM n WHERE extract(year from mth) BETWEEN 2000 AND 2025 GROUP BY 1),
y AS (SELECT extract(year from mth)::int yr, avg(v) v FROM n WHERE extract(year from mth) BETWEEN 2000 AND 2025 GROUP BY 1)
SELECT (SELECT round(v::numeric,2) FROM n WHERE mth='2026-09-01') sep26, (SELECT string_agg(to_char(mth,'YYYY-MM')||':'||round(v::numeric,2), ' ' ORDER BY mth) FROM n WHERE mth >= '2025-10-01') series,
 (SELECT string_agg(mo||':'||round(v::numeric,2), ' ' ORDER BY mo) FROM lt) lt_by_month, (SELECT round(v::numeric,2) FROM n WHERE mth='2024-10-01') oct24, (SELECT round(v::numeric,2) FROM n WHERE mth='2025-03-01') mar25,
 (SELECT round(v::numeric,2) FROM y WHERE yr=2000) y2000, (SELECT round(v::numeric,2) FROM y WHERE yr=2025) y2025, (SELECT round(avg(v)::numeric,2) FROM y) lt_mean,
 (SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.v-a.v)/(b.yr-a.yr)) * 10)::numeric,3) FROM y a JOIN y b ON b.yr > a.yr) sen10, (SELECT count(*) FROM n) n_months, (SELECT count(*) FROM n WHERE mth >= '2022-01-01') n_months_since_2022;;
-- C. 12-month change per district: mean of monthly means Oct 2025-Sep 2026 minus Oct 2024-Sep 2025 (+ = deeper); count rising (< 0); the largest declines; Sep 2026 depth classes; deepest / shallowest
WITH {W}, wl AS (SELECT DISTINCT lower(name) lname, code, prov, area FROM w),
m AS (SELECT wl.code, date_trunc('month', date)::date mth, avg(value) v FROM "10_1_borehole_and_aquifer_master" t JOIN wl ON wl.lname=lower(t.district_name) WHERE variable_name='groundwater_table_depth_m_bgl' AND date <= '2026-09-30' GROUP BY 1,2),
ch AS (SELECT code, avg(v) FILTER (WHERE mth > '2025-09-01' AND mth <= '2026-09-01') - avg(v) FILTER (WHERE mth > '2024-09-01' AND mth <= '2025-09-01') d FROM m GROUP BY 1),
cur AS (SELECT code, v FROM m WHERE mth='2026-09-01')
SELECT (SELECT count(*) FROM ch WHERE d < 0) rising, (SELECT string_agg(code||':'||round((-d)::numeric,2), ' ' ORDER BY d DESC) FROM (SELECT * FROM ch ORDER BY d DESC LIMIT 6) x) largest_declines,
 (SELECT string_agg(code||':'||round((-d)::numeric,2), ' ' ORDER BY code) FROM ch WHERE code IN ('CPT','DC34','DC44','DC12','DC1')) change_sample,
 (SELECT string_agg(c||'='||n, ' ' ORDER BY c) FROM (SELECT CASE WHEN v < 5 THEN '1:<5' WHEN v < 10 THEN '2:5-10' WHEN v < 20 THEN '3:10-20' WHEN v < 30 THEN '4:20-30' WHEN v < 50 THEN '5:30-50' WHEN v < 100 THEN '6:50-100' ELSE '7:>100' END c, count(*) n FROM cur GROUP BY 1) k) classes,
 (SELECT string_agg(code||':'||round(v::numeric,1), ' ' ORDER BY v DESC) FROM (SELECT * FROM cur ORDER BY v DESC LIMIT 3) x) deepest, (SELECT string_agg(code||':'||round(v::numeric,1), ' ' ORDER BY v) FROM (SELECT * FROM cur ORDER BY v LIMIT 3) x) shallowest,
 (SELECT string_agg(code||':'||round(v::numeric,1), ' ' ORDER BY code) FROM cur WHERE code IN ('CPT','DC34','DC44','BUF')) level_sample;;
-- C2. Sen's slope of annual means per district (m/yr, + = deeper) for DC1, DC47, DC4
WITH {W}, wl AS (SELECT DISTINCT lower(name) lname, code, prov, area FROM w),
m AS (SELECT wl.code, date_trunc('month', date)::date mth, avg(value) v FROM "10_1_borehole_and_aquifer_master" t JOIN wl ON wl.lname=lower(t.district_name) WHERE variable_name='groundwater_table_depth_m_bgl' AND date <= '2025-12-31' AND wl.code IN ('DC1','DC47','DC4') GROUP BY 1,2),
y AS (SELECT code, extract(year from mth)::int yr, avg(v) v FROM m WHERE extract(year from mth) BETWEEN 2000 AND 2025 GROUP BY 1,2)
SELECT a.code, round((-percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.v-a.v)/(b.yr-a.yr)))::numeric,3) trend_rising FROM y a JOIN y b ON b.yr > a.yr AND b.code=a.code GROUP BY 1 ORDER BY 1;;
-- D. recharge KPI (11.2 direct recharge 2025 area-weighted), recharge volume sum (mm x area / 1000), abstraction sum (11.3) 2024 / 2025; districts
WITH {W}, {WC}, r AS (SELECT w.code, year, max("Direct recharge") dr FROM "11.2_recharge_and_discharge" t JOIN w ON lower(w.name)=lower(t.district_name) WHERE year IN (2024, 2025) GROUP BY 1,2),
a AS (SELECT w.code, year, max("Total abstraction") ab FROM "11.3_aquifer_sustainability" t JOIN w ON lower(w.name)=lower(t.district_name) WHERE year IN (2024, 2025) GROUP BY 1,2)
SELECT r.year, round((sum(wc.area*dr)/sum(wc.area))::numeric,1) recharge_mm, round(sum(dr*wc.area/1000)::numeric,1) recharge_mm3, round((sum(ab)/1e6)::numeric,1) abstraction_mm3, count(*) n,
 (SELECT string_agg(r2.code||':'||round(dr::numeric,0)||'/'||round((ab/1e6)::numeric,1), ' ' ORDER BY r2.code) FROM r r2 JOIN a a2 ON a2.code=r2.code AND a2.year=r2.year WHERE r2.year=r.year AND r2.code IN ('CPT','DC34','DC44')) d FROM r JOIN a ON a.code=r.code AND a.year=r.year JOIN wc ON wc.code=r.code GROUP BY 1 ORDER BY 1;;
-- E. SGI map Aug 2024 and entity DC44 fields (heads Sep 2026, 2025 attributes, 10_2 vadose SM Sep 2026)
SELECT district_name, round(sgi_dimensionless::numeric,2) FROM hydrological_drought_master WHERE date='2024-08-01' AND district_name IN ('cape town','vhembe','alfred nzo');;
SELECT variable_name, round(avg(value)::numeric,1) v, count(*) n FROM "10_1_borehole_and_aquifer_master" WHERE district_name='Alfred Nzo' AND date BETWEEN '2026-09-01' AND '2026-09-30' AND variable_name IN ('hydraulic_head_amsl_m','hydraulic_head_m_amsl','groundwater_table_depth_m_bgl') GROUP BY 1;;
SELECT variable_name, round(avg(value)::numeric,1) v, count(*) n, count(DISTINCT value) nd FROM "10_1_borehole_and_aquifer_master" WHERE district_name='Alfred Nzo' AND date BETWEEN '2025-01-01' AND '2025-12-31' AND variable_name IN ('recharge_fraction_percent','borehole_pumping_rate_m3_h','annual_abstraction_volume_m3_yr') GROUP BY 1;;
