-- J3. per-district monthly mean flow (9_1 daily_mean_flow m3/day -> m3/s), case-insensitive district match, 2000-01..2026-08
WITH {W} SELECT code, string_agg(to_char(mth,'YYYY-MM')||':'||round(q::numeric,5)||'/'||n, ',' ORDER BY mth) FROM (SELECT w.code, date_trunc('month', date)::date mth, avg(daily_mean_flow)/86400 q, count(daily_mean_flow) n FROM "9_1_hydrological_station_master" t JOIN w ON lower(w.name)=lower(t.district_name) WHERE date BETWEEN '2000-01-01' AND '2026-08-31' GROUP BY 1,2) x GROUP BY 1 ORDER BY 1;;
-- G1. groundwater depth anomaly Sep 2026 (case-insensitive match)
WITH {W}, m AS (SELECT w.code, w.area, date_trunc('month', date)::date mth, avg(value) v, count(*) n FROM "10_1_borehole_and_aquifer_master" t JOIN w ON lower(w.name)=lower(t.district_name) WHERE variable_name='groundwater_table_depth_m_bgl' AND date <= CURRENT_DATE GROUP BY 1,2,3),
cur AS (SELECT code, area, v FROM m WHERE mth='2026-09-01'),
cl AS (SELECT code, avg(v) n, count(*) yrs FROM m WHERE extract(month from mth)=9 AND extract(year from mth) BETWEEN 2000 AND 2025 GROUP BY 1)
SELECT round((sum(cur.area*(cl.n-cur.v))/sum(cur.area))::numeric,3) anomaly_m, count(*) n_districts, round((sum(cur.area)/1222435.4*100)::numeric,1) coverage_pct, min(yrs) min_yrs,
 (SELECT string_agg(code||':'||round((cl.n-cur.v)::numeric,2), ' ' ORDER BY code) FROM cur JOIN cl USING (code) WHERE code IN ('CPT','DC34','DC44')) d FROM cur JOIN cl USING (code);;
-- G2. months sampled in 10_1 depth telemetry in 2026
SELECT date_trunc('month', date)::date mth, count(DISTINCT district_name) n_d, count(*) n, max(date) FROM "10_1_borehole_and_aquifer_master" WHERE variable_name='groundwater_table_depth_m_bgl' AND date >= '2026-01-01' AND date <= CURRENT_DATE GROUP BY 1 ORDER BY 1;;
-- G3. distinct spellings in 10_1 and 9_1 not matched case-insensitively
WITH {W} SELECT '10_1' t, d FROM (SELECT DISTINCT district_name d FROM "10_1_borehole_and_aquifer_master") x WHERE lower(d) NOT IN (SELECT lower(name) FROM w)
UNION ALL SELECT '9_1', d FROM (SELECT DISTINCT district_name d FROM "9_1_hydrological_station_master") x WHERE lower(d) NOT IN (SELECT lower(name) FROM w);;
