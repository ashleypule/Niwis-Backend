-- A. KPIs over the 365-day window: depth (area-weighted), anomaly vs the 7.2 normal (depth / (1 + anomaly/100), median per district-month, pro-rated), BFI and coefficient (district ratios area-weighted AND ratio of sums)
WITH {W},
d AS (SELECT w.code, w.area, sum("Daily_Surface_Runoff_Volume_m3_day")/6.5e9*1000 q, sum("Baseflow_Component_m3s")*86400 bf, sum("Daily_Surface_Runoff_Volume_m3_day") vol, count(*) n
      FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2025-09-07' AND '2026-09-06' GROUP BY 1,2),
p AS (SELECT w.code, sum("Daily_Rainfall_Total_mm") p FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2025-09-07' AND '2026-09-06' GROUP BY 1),
md AS (SELECT w.code, date_trunc('month',"Date")::date mth, sum("Daily_Surface_Runoff_Volume_m3_day")/6.5e9*1000 q, count(*) n FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" GROUP BY 1,2),
an AS (SELECT w.code, make_date("Year","Month",1) mth, "Runoff_Anomaly_percent" a FROM "7.2_derived_runoff_indicators_master" t JOIN w ON w.name=t."District_Name"),
nm AS (SELECT md.code, extract(month from md.mth) mo, percentile_cont(0.5) WITHIN GROUP (ORDER BY md.q/(1+an.a/100)) n, count(DISTINCT round((md.q/(1+an.a/100))::numeric,3)) nd
       FROM md JOIN an ON an.code=md.code AND an.mth=md.mth WHERE md.n = extract(day from (md.mth + interval '1 month - 1 day')) AND an.a > -100 GROUP BY 1,2),
days AS (SELECT d::date d FROM generate_series('2025-09-07'::date,'2026-09-06'::date, interval '1 day') d),
frac AS (SELECT extract(month from d) mo, sum(1.0/extract(day from (date_trunc('month', d) + interval '1 month - 1 day'))) f FROM days GROUP BY 1),
nw AS (SELECT nm.code, sum(nm.n * frac.f) n FROM nm JOIN frac ON frac.mo = nm.mo GROUP BY 1)
SELECT round((sum(d.area*d.q)/sum(d.area))::numeric,1) depth, round(((sum(d.area*d.q)-sum(d.area*nw.n))/sum(d.area*nw.n)*100)::numeric,1) anomaly, round((sum(d.area*nw.n)/sum(d.area))::numeric,1) normal,
 round((sum(d.area*(d.bf/d.vol))/sum(d.area))::numeric,3) bfi_mean_of_ratios, round((sum(d.area*d.bf)/sum(d.area*d.vol))::numeric,3) bfi_ratio_of_sums,
 round((sum(d.area*(d.q/p.p))/sum(d.area))::numeric,3) coef_mean_of_ratios, round((sum(d.area*d.q)/sum(d.area*p.p))::numeric,3) coef_ratio_of_sums, (SELECT max(nd) FROM nm) normal_distinct_per_district_month,
 (SELECT string_agg(d.code||':'||round(d.q::numeric,1)||'/'||round(((d.q-nw.n)/nw.n*100)::numeric,1)||'/'||round((d.bf/d.vol)::numeric,2)||'/'||round((d.q/p.p)::numeric,3), ' ' ORDER BY d.code) FROM d JOIN nw USING (code) JOIN p USING (code) WHERE d.code IN ('CPT','DC34','DC44')) d_depth_anom_bfi_coef
FROM d JOIN nw USING (code) JOIN p USING (code);;
-- B. monthly runoff anomaly series (national): weighted depth vs weighted normal; and the area-weighted mean of the stored 7.2 anomaly (the forecast target) for comparison
WITH {W},
md AS (SELECT w.code, w.area, date_trunc('month',"Date")::date mth, sum("Daily_Surface_Runoff_Volume_m3_day")/6.5e9*1000 q, count(*) n FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE "Date" <= '2026-08-31' GROUP BY 1,2,3),
an AS (SELECT w.code, w.area, make_date("Year","Month",1) mth, "Runoff_Anomaly_percent" a FROM "7.2_derived_runoff_indicators_master" t JOIN w ON w.name=t."District_Name"),
nm AS (SELECT md.code, md.area, extract(month from md.mth) mo, percentile_cont(0.5) WITHIN GROUP (ORDER BY md.q/(1+an.a/100)) n FROM md JOIN an ON an.code=md.code AND an.mth=md.mth WHERE md.n = extract(day from (md.mth + interval '1 month - 1 day')) AND an.a > -100 GROUP BY 1,2,3),
nat AS (SELECT mth, sum(area*q)/sum(area) q FROM md GROUP BY 1),
nn AS (SELECT mo, sum(area*n)/sum(area) n FROM nm GROUP BY 1),
s AS (SELECT nat.mth, (nat.q-nn.n)/nn.n*100 a FROM nat JOIN nn ON nn.mo=extract(month from nat.mth)),
r AS (SELECT mth, a, avg(a) OVER (ORDER BY mth ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) ma12 FROM s),
st AS (SELECT mth, sum(area*a)/sum(area) stored_mean FROM an GROUP BY 1)
SELECT to_char(r.mth,'YYYY-MM') m, round(r.a::numeric,1) anomaly, round(r.ma12::numeric,1) ma12, round(st.stored_mean::numeric,1) stored_72_mean FROM r JOIN st USING (mth) WHERE r.mth >= '2025-09-01' ORDER BY 1;;
-- C. discharge: national daily flow (sum of depth x area x 1000 / 86400) on 2025-09-07 and 2026-09-06; Sep 2025 monthly mean; long-term Sep mean 2000-2025; Aug 2026 mean
WITH {W}, f AS (SELECT "Date" d, sum("Daily_Surface_Runoff_Volume_m3_day"/6.5e9*1000*w.area*1000/86400) q, count(*) n FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" GROUP BY 1)
SELECT (SELECT round(q::numeric,2) FROM f WHERE d='2025-09-07') d1, (SELECT round(q::numeric,2) FROM f WHERE d='2026-09-06') d2, (SELECT round(avg(q)::numeric,2) FROM f WHERE d BETWEEN '2025-09-01' AND '2025-09-30') sep25,
 (SELECT round(avg(q)::numeric,2) FROM f WHERE extract(month from d)=9 AND extract(year from d) BETWEEN 2000 AND 2025) lt_sep, (SELECT round(avg(q)::numeric,2) FROM f WHERE d BETWEEN '2026-08-01' AND '2026-08-31') aug26, (SELECT round(avg(q)::numeric,2) FROM f WHERE d BETWEEN '2026-01-01' AND '2026-01-31') jan26,
 (SELECT round(avg(q)::numeric,2) FROM f WHERE extract(month from d)=1 AND extract(year from d) BETWEEN 2000 AND 2025) lt_jan, (SELECT min(n) FROM f) min_districts_per_day,
 (SELECT round(percentile_cont(0.5) WITHIN GROUP (ORDER BY q)::numeric,3) FROM f WHERE d BETWEEN '2025-09-07' AND '2026-09-06') fdc_last_p50, (SELECT round(max(q)::numeric,3) FROM f WHERE d BETWEEN '2025-09-07' AND '2026-09-06') fdc_last_max,
 (SELECT round(percentile_cont(0.5) WITHIN GROUP (ORDER BY q)::numeric,3) FROM f WHERE d BETWEEN '2000-01-01' AND '2025-12-31') fdc_lt_p50, (SELECT round(max(q)::numeric,3) FROM f WHERE d BETWEEN '2000-01-01' AND '2025-12-31') fdc_lt_max;;
-- C2. annual maxima of national daily flow 2000-2025 (for the LP3 refit)
WITH {W}, f AS (SELECT "Date" d, sum("Daily_Surface_Runoff_Volume_m3_day"/6.5e9*1000*w.area*1000/86400) q FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2000-01-01' AND '2025-12-31' GROUP BY 1)
SELECT string_agg(y||':'||round(mx::numeric,1), ',' ORDER BY y) FROM (SELECT extract(year from d) y, max(q) mx FROM f GROUP BY 1) x;;
-- D. national volume (Mm3) Jan 2026 and long-term Jan (2000-2025); seasonal (mean flow) covered by C
WITH {W}, v AS (SELECT date_trunc('month',"Date")::date mth, sum("Daily_Surface_Runoff_Volume_m3_day"/6.5e9*1000*w.area/1000) v FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" GROUP BY 1)
SELECT (SELECT round(v::numeric,2) FROM v WHERE mth='2026-01-01') jan26, (SELECT round(v::numeric,2) FROM v WHERE mth='2026-08-01') aug26, (SELECT round(avg(v)::numeric,2) FROM v WHERE extract(month from mth)=1 AND extract(year from mth) BETWEEN 2000 AND 2025) lt_jan, (SELECT round(v::numeric,2) FROM v WHERE mth='2025-12-01') dec25;;
-- E. by-WMA volumes over the window: current = sum of district depth x overlap area / 1000; long-term = normal (as A) x overlap
WITH {W}, {WMA},
d AS (SELECT w.code, sum("Daily_Surface_Runoff_Volume_m3_day")/6.5e9*1000 q FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2025-09-07' AND '2026-09-06' GROUP BY 1),
md AS (SELECT w.code, date_trunc('month',"Date")::date mth, sum("Daily_Surface_Runoff_Volume_m3_day")/6.5e9*1000 q, count(*) n FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" GROUP BY 1,2),
an AS (SELECT w.code, make_date("Year","Month",1) mth, "Runoff_Anomaly_percent" a FROM "7.2_derived_runoff_indicators_master" t JOIN w ON w.name=t."District_Name"),
nm AS (SELECT md.code, extract(month from md.mth) mo, percentile_cont(0.5) WITHIN GROUP (ORDER BY md.q/(1+an.a/100)) n FROM md JOIN an ON an.code=md.code AND an.mth=md.mth WHERE md.n = extract(day from (md.mth + interval '1 month - 1 day')) AND an.a > -100 GROUP BY 1,2),
days AS (SELECT d::date d FROM generate_series('2025-09-07'::date,'2026-09-06'::date, interval '1 day') d),
frac AS (SELECT extract(month from d) mo, sum(1.0/extract(day from (date_trunc('month', d) + interval '1 month - 1 day'))) f FROM days GROUP BY 1),
nw AS (SELECT nm.code, sum(nm.n * frac.f) n FROM nm JOIN frac ON frac.mo = nm.mo GROUP BY 1)
SELECT wo.wma, round(sum(d.q*wo.area/1000)::numeric,0) current_mm3, round(sum(nw.n*wo.area/1000)::numeric,0) lt_mm3, count(*) n_districts FROM wo JOIN d USING (code) JOIN nw USING (code) GROUP BY 1 ORDER BY 1;;
-- F. high-flow events: district Q10 (90th percentile of daily depth 2000-2025), exceedances in the window, flow = depth x area x 1000 / 86400 (top 5)
WITH {W}, q10 AS (SELECT w.code, w.area, percentile_cont(0.9) WITHIN GROUP (ORDER BY "Daily_Surface_Runoff_Volume_m3_day") q FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2000-01-01' AND '2025-12-31' GROUP BY 1,2),
e AS (SELECT w.code, "Date" d, "Daily_Surface_Runoff_Volume_m3_day" v FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2025-09-07' AND '2026-09-06')
SELECT e.code, e.d, round((e.v/6.5e9*1000*q10.area*1000/86400)::numeric,1) flow_m3s, (SELECT count(*) FROM e e2 JOIN q10 q2 ON q2.code=e2.code WHERE e2.v > q2.q) n_events FROM e JOIN q10 ON q10.code=e.code WHERE e.v > q10.q ORDER BY 3 DESC LIMIT 5;;
-- G. top districts Aug 2026: mean daily depth x area x 1000 / 86400 and the change vs Aug 2025
WITH {W}, m AS (SELECT w.code, w.area, avg("Daily_Surface_Runoff_Volume_m3_day"/6.5e9*1000)*w.area*1000/86400 q, extract(year from "Date") y FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE extract(month from "Date")=8 AND extract(year from "Date") IN (2025, 2026) GROUP BY 1,2,4)
SELECT a.code, round(a.q::numeric,1) aug26, round((a.q-b.q)::numeric,1) vs_last_year FROM m a JOIN m b ON b.code=a.code AND b.y=2025 WHERE a.y=2026 AND a.code IN ('DC30','DC19','DC45','CPT','DC44','DC34') ORDER BY 2 DESC;;
-- H. statistics: annual coefficient = weighted annual depth / weighted annual rainfall 2000-2025: mean and Sen's slope x10; runoff trend
WITH {W}, q AS (SELECT w.code, w.area, extract(year from "Date")::int yr, sum("Daily_Surface_Runoff_Volume_m3_day")/6.5e9*1000 q FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE extract(year from "Date") BETWEEN 2000 AND 2025 GROUP BY 1,2,3),
p AS (SELECT w.code, "Year" yr, sum("Daily_Rainfall_Total_mm") p FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" BETWEEN 2000 AND 2025 GROUP BY 1,2),
y AS (SELECT q.yr, (sum(q.area*q.q)/sum(q.area)) / (sum(q.area*p.p)/sum(q.area)) c, sum(q.area*q.q)/sum(q.area) qq FROM q JOIN p USING (code, yr) GROUP BY 1)
SELECT round(avg(c)::numeric,3) coef_clim, (SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.c-a.c)/(b.yr-a.yr)) * 10)::numeric,4) FROM y a JOIN y b ON b.yr > a.yr) coef_sen10, round(avg(qq)::numeric,1) q_clim, (SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.qq-a.qq)/(b.yr-a.yr)) * 10)::numeric,2) FROM y a JOIN y b ON b.yr > a.yr) q_sen10 FROM y;;
-- I. maps: monthly runoff Sep 2026 (1-6 Sep) DC42/CPT/DC34/DC44; runoff coefficient 2000-2025 per district (sum q / sum p) BUF/CPT/DC34/DC44
WITH {W} SELECT w.code, round((sum("Daily_Surface_Runoff_Volume_m3_day")/6.5e9*1000)::numeric,1) sep26 FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2026-09-01' AND '2026-09-06' AND w.code IN ('DC42','CPT','DC34','DC44') GROUP BY 1 ORDER BY 1;;
WITH {W}, q AS (SELECT w.code, sum("Daily_Surface_Runoff_Volume_m3_day")/6.5e9*1000 q FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2000-01-01' AND '2025-12-31' AND w.code IN ('BUF','CPT','DC34','DC44') GROUP BY 1),
p AS (SELECT w.code, sum("Daily_Rainfall_Total_mm") p FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" BETWEEN 2000 AND 2025 AND w.code IN ('BUF','CPT','DC34','DC44') GROUP BY 1)
SELECT q.code, round((q.q/p.p)::numeric,3) coef FROM q JOIN p USING (code) ORDER BY 1;;
-- J. water balance AET Aug 2026 and Sep 2025 (sum per district-month, area-weighted)
WITH {W}, a AS (SELECT w.code, w.area, date_trunc('month',"Date")::date mth, sum("Actual_ET_from_Catchment_mm") aet, count(*) n FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2025-09-01' AND '2026-08-31' GROUP BY 1,2,3)
SELECT to_char(mth,'YYYY-MM') m, round((sum(area*aet)/sum(area))::numeric,1) aet FROM a WHERE mth IN ('2025-09-01','2026-08-01') GROUP BY 1 ORDER BY 1;;
