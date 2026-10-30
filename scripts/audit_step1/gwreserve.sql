-- A. 11.2 / 11.3 / 11_1 structure
SELECT table_name, string_agg(column_name, ', ' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_name IN ('11.2_recharge_and_discharge','11.3_aquifer_sustainability','11_1_hydrogeological') GROUP BY 1;;
-- B. 2025 KPIs: total recharge = sum(direct mm x area/1000 + indirect/1e6); abstraction; dynamic / static reserve sums; EI area-weighted mean (%); risk (EI >= 50 %); class counts; districts
WITH {W}, wl AS (SELECT DISTINCT lower(name) lname, code, prov, area FROM w),
r AS (SELECT wl.code, wl.area, max("Direct recharge") dr, max("Indirect recharge") ir FROM "11.2_recharge_and_discharge" t JOIN wl ON wl.lname=lower(t.district_name) WHERE year=2025 GROUP BY 1,2),
a AS (SELECT wl.code, max("Total abstraction") ab, max("Exploitation index") ei FROM "11.3_aquifer_sustainability" t JOIN wl ON wl.lname=lower(t.district_name) WHERE year=2025 GROUP BY 1),
h AS (SELECT wl.code, max("Static reserve volume") st, max("Dynamic (renewable) reserve") dyn FROM "11_1_hydrogeological" t JOIN wl ON wl.lname=lower(t.district_name) WHERE year=2025 GROUP BY 1),
j AS (SELECT r.code, r.area, dr*area/1000 + ir/1e6 rech, ab/1e6 abst, ei, st, dyn FROM r JOIN a USING (code) JOIN h USING (code))
SELECT round(sum(rech)::numeric,0) recharge, round(sum(abst)::numeric,0) abstraction, round(sum(dyn)::numeric,0) dynamic, round(sum(st)::numeric,0) static, round((sum(area*ei)/sum(area))::numeric,1) ei_wmean, count(*) FILTER (WHERE ei >= 50) risk, count(*) n,
 (SELECT string_agg(c||'='||n, ' ' ORDER BY c) FROM (SELECT CASE WHEN ei < 10 THEN '1:VeryLow' WHEN ei < 30 THEN '2:Low' WHEN ei < 50 THEN '3:Moderate' WHEN ei < 70 THEN '4:High' WHEN ei <= 100 THEN '5:VeryHigh' ELSE '6:Over' END c, count(*) n FROM j GROUP BY 1) x) classes,
 (SELECT string_agg(code||':'||round(rech::numeric,1)||'/'||round(abst::numeric,1)||'/'||round((ei/100)::numeric,3)||'/'||round(st::numeric,0), ' ' ORDER BY ei DESC) FROM (SELECT * FROM j ORDER BY ei DESC LIMIT 3) x) top_ei,
 (SELECT string_agg(code||':'||round(rech::numeric,0)||'/'||round(abst::numeric,1)||'/'||round(ei::numeric,1)||'/'||round(st::numeric,0)||'/'||round(dyn::numeric,0), ' ' ORDER BY code) FROM j WHERE code IN ('CPT','DC34','DC44')) d_rech_abst_ei_static_dyn FROM j;;
-- C. 11_1 depth to water: monthly national area-weighted series Oct 2025 - Sep 2026, annual means 2000-2025, lt mean, Sen's slope and MK intercept, trend line at Oct 2025 and Sep 2026
WITH {W}, wl AS (SELECT DISTINCT lower(name) lname, code, prov, area FROM w),
m AS (SELECT wl.code, wl.area, make_date(year, month, 1) mth, max(CASE WHEN variable = 'Depth to water table' THEN value END) v FROM "11_1_hydrogeological" t JOIN wl ON wl.lname=lower(t.district_name) WHERE make_date(year, month, 1) <= '2026-09-01' GROUP BY 1,2,3),
n AS (SELECT mth, sum(area*v)/sum(area) v, count(*) nd FROM m GROUP BY 1),
y AS (SELECT extract(year from mth)::int yr, avg(v) v FROM n WHERE extract(year from mth) BETWEEN 2000 AND 2025 GROUP BY 1),
s AS (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.v-a.v)/(b.yr-a.yr)) slope FROM y a JOIN y b ON b.yr > a.yr),
i AS (SELECT (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY v) FROM y) - 12.5*slope icpt, slope FROM s)
SELECT (SELECT string_agg(to_char(mth,'YYYY-MM')||':'||round(v::numeric,2), ' ' ORDER BY mth) FROM n WHERE mth >= '2025-10-01') series, (SELECT round(avg(v)::numeric,2) FROM n WHERE extract(year from mth) BETWEEN 2000 AND 2025) lt_mean_months, (SELECT round(avg(v)::numeric,2) FROM y) lt_mean_years,
 round((slope*10)::numeric,3) sen10, round(icpt::numeric,2) intercept, round((icpt + slope*(25 + 9.5/12))::numeric,2) line_oct25, round((icpt + slope*(26 + 8.5/12))::numeric,2) line_sep26, (SELECT string_agg(yr||':'||round(v::numeric,2), ' ' ORDER BY yr) FROM y WHERE yr IN (2000,2001,2012,2024,2025)) annual_sample, (SELECT min(nd)||'-'||max(nd) FROM n) districts FROM i;;
-- D. maps 2025 / Sep 2026: EI ratio, direct recharge, 11_1 depth (BUF, CPT, DC1, DC44, DC34)
WITH {W}, wl AS (SELECT DISTINCT lower(name) lname, code, prov, area FROM w)
SELECT wl.code, (SELECT round((max("Exploitation index")/100)::numeric,3) FROM "11.3_aquifer_sustainability" x WHERE lower(x.district_name)=wl.lname AND year=2025) ei, (SELECT round(max("Direct recharge")::numeric,0) FROM "11.2_recharge_and_discharge" x WHERE lower(x.district_name)=wl.lname AND year=2025) recharge, (SELECT round(max(CASE WHEN variable = 'Depth to water table' THEN value END)::numeric,1) FROM "11_1_hydrogeological" x WHERE lower(x.district_name)=wl.lname AND year=2026 AND month=9) depth_sep26 FROM wl WHERE wl.code IN ('BUF','CPT','DC1','DC44','DC34') ORDER BY 1;;
-- E. entity DC44 (2025): aquifer extent, saturated thickness, transmissivity, specific yield, static, dynamic, direct recharge, borehole yield, depletion rate
SELECT year, "Aquifer extent (map area)" extent, "Aquifer saturated thickness" thick, "Transmissivity" t, "Specific yield" sy, "Static reserve volume" st, "Dynamic (renewable) reserve" dyn FROM "11_1_hydrogeological" WHERE district_name='Alfred Nzo' AND year=2025 AND month=12;;
SELECT year, "Borehole yield" y, "Aquifer depletion rate" dep, "Exploitation index" ei, "Total abstraction" ab FROM "11.3_aquifer_sustainability" WHERE district_name='Alfred Nzo' AND year=2025 LIMIT 2;;
