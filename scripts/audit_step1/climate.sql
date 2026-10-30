-- A. temperature anomaly, 12 complete months to Aug 2026: stored monthly anomaly (4_1), per district mean, area-weighted; plus the same vs a 2000-2025 full-year baseline of Tavg
WITH {W}, m AS (SELECT w.code, w.area, left("Date",7) ym, max("Temperature_Anomaly_C") a, count(DISTINCT "Temperature_Anomaly_C") nd, avg("Tavg_C") t FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" GROUP BY 1,2,3),
win AS (SELECT code, area, avg(a) a12, max(nd) nd FROM m WHERE ym BETWEEN '2025-09' AND '2026-08' GROUP BY 1,2),
clim AS (SELECT code, right(ym,2) mo, avg(t) tn FROM m WHERE left(ym,4)::int BETWEEN 2000 AND 2025 GROUP BY 1,2),
alt AS (SELECT m.code, m.area, avg(m.t - clim.tn) a12b FROM m JOIN clim ON clim.code=m.code AND clim.mo=right(m.ym,2) WHERE ym BETWEEN '2025-09' AND '2026-08' GROUP BY 1,2)
SELECT round((sum(win.area*a12)/sum(win.area))::numeric,3) anom_stored, max(nd) anomaly_distinct_per_month, round((sum(alt.area*a12b)/sum(alt.area))::numeric,3) anom_vs_2000_2025,
 (SELECT string_agg(code||':'||round(a12::numeric,2), ' ' ORDER BY code) FROM win WHERE code IN ('CPT','DC34','DC44','BUF')) d FROM win JOIN alt USING (code);;
-- B. annual mean Tavg (sum/days per district-year), area-weighted: 2000, 2024, 2025, mean 2000-2025, sd, Sen's slope x10, MK-style intercept, 2030/2050/2070 extrapolation
WITH {W}, y AS (SELECT w.code, w.area, left("Date",4)::int yr, sum("Tavg_C")/count("Tavg_C") t, count(*) days FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE left("Date",4)::int BETWEEN 2000 AND 2025 GROUP BY 1,2,3),
a AS (SELECT yr, sum(area*t)/sum(area) v, min(days) mind, max(days) maxd FROM y GROUP BY 1),
s AS (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.v-a.v)/(b.yr-a.yr)) slope FROM a a JOIN a b ON b.yr > a.yr),
i AS (SELECT (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY v) FROM a) - 12.5 * slope icpt, slope FROM s)
SELECT (SELECT round(v::numeric,2) FROM a WHERE yr=2000) t2000, (SELECT round(v::numeric,2) FROM a WHERE yr=2024) t2024, (SELECT round(v::numeric,2) FROM a WHERE yr=2025) t2025, round(avg(v)::numeric,2) mean, round(stddev_samp(v)::numeric,3) sd,
 round((slope*10)::numeric,3) sen10, round((icpt + slope*30)::numeric,2) y2030, round((icpt + slope*50)::numeric,2) y2050, round((icpt + slope*70)::numeric,2) y2070, min(mind) min_days, max(maxd) max_days FROM a, i GROUP BY slope, icpt;;
-- B2. per-district Sen's slope of annual mean Tavg (map temp-trend) and the 2000-2025 mean (map mean-temp) for CPT, DC34, DC44
WITH {W}, y AS (SELECT w.code, left("Date",4)::int yr, avg("Tavg_C") t FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE left("Date",4)::int BETWEEN 2000 AND 2025 AND w.code IN ('CPT','DC34','DC44') GROUP BY 1,2)
SELECT a.code, round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.t-a.t)/(b.yr-a.yr)) * 10)::numeric,3) sen10, (SELECT round(avg(t)::numeric,1) FROM y WHERE y.code=a.code) mean FROM y a JOIN y b ON b.yr > a.yr AND b.code=a.code GROUP BY 1 ORDER BY 1;;
-- C. rainfall change 2025 vs 2000-2025 (4_3 MAP_mm): national and districts; map rain-trend 2016-2025 vs 2000-2015
WITH {W}, y AS (SELECT w.code, w.area, "Year" yr, "MAP_mm" v FROM "4_3_mean_annual_precipitation_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" BETWEEN 2000 AND 2025),
n AS (SELECT yr, sum(area*v)/sum(area) v FROM y GROUP BY 1)
SELECT round((((SELECT v FROM n WHERE yr=2025) - avg(v))/avg(v)*100)::numeric,1) nat_change, round(avg(v)::numeric,0) nat_mean,
 (SELECT string_agg(code||':'||ch||'('||mn||')', ' ' ORDER BY code) FROM (SELECT code, round(((max(v) FILTER (WHERE yr=2025) - avg(v))/avg(v)*100)::numeric,1) ch, round(avg(v)::numeric,0) mn FROM y WHERE code IN ('CPT','DC34','DC44') GROUP BY 1) d) districts,
 (SELECT string_agg(code||':'||round(((r-e)/e*100)::numeric,1), ' ' ORDER BY code) FROM (SELECT code, avg(v) FILTER (WHERE yr BETWEEN 2016 AND 2025) r, avg(v) FILTER (WHERE yr BETWEEN 2000 AND 2015) e FROM y WHERE code IN ('CPT','DC34','DC44','BUF') GROUP BY 1) x) rain_trend_map FROM n;;
-- D. ET0 / PET annual sums (meteorological_drought_master), AET (runoff table): 2000, 2025, mean, days per year; Sen's slope ET0; districts
WITH {W}, y AS (SELECT w.code, w.area, year yr, sum(et0_mm_day) et0, sum(pet_mm_day) pet, count(et0_mm_day) days FROM meteorological_drought_master t JOIN w ON w.name=t.district_name WHERE year BETWEEN 2000 AND 2025 GROUP BY 1,2,3),
a AS (SELECT yr, sum(area*et0)/sum(area) et0, sum(area*pet)/sum(area) pet, min(days) mind, max(days) maxd FROM y GROUP BY 1),
q AS (SELECT w.code, w.area, left("Date",4)::int yr, sum("Actual_ET_from_Catchment_mm") aet FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE left("Date",4)::int BETWEEN 2000 AND 2025 GROUP BY 1,2,3),
qa AS (SELECT yr, sum(area*aet)/sum(area) aet FROM q GROUP BY 1)
SELECT (SELECT round(et0::numeric,1)||'/'||round(pet::numeric,0)||'/'||mind||'-'||maxd FROM a WHERE yr=2000) et0_pet_days_2000, (SELECT round(et0::numeric,1)||'/'||round(pet::numeric,0)||'/'||mind||'-'||maxd FROM a WHERE yr=2025) et0_pet_days_2025,
 round(avg(et0)::numeric,1) et0_mean, round(((SELECT et0 FROM a WHERE yr=2025) - avg(et0))::numeric,0) et0_delta, round(stddev_samp(et0)::numeric,1) et0_sd,
 (SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.et0-a.et0)/(b.yr-a.yr)) * 10)::numeric,1) FROM a a JOIN a b ON b.yr > a.yr) et0_sen10,
 (SELECT round(aet::numeric,0) FROM qa WHERE yr=2000) aet2000, (SELECT round(aet::numeric,0) FROM qa WHERE yr=2025) aet2025,
 (SELECT string_agg(code||':'||e25||'('||round(d::numeric,0)||')/'||mn, ' ' ORDER BY code) FROM (SELECT code, round(max(et0) FILTER (WHERE yr=2025)::numeric,0) e25, max(et0) FILTER (WHERE yr=2025) - avg(et0) d, round(avg(et0)::numeric,0) mn FROM y WHERE code IN ('CPT','DC34','DC44') GROUP BY 1) x) districts_et0_2025_delta_mean FROM a;;
-- E. annual runoff depth (mm) national + districts: 2000, 2025, mean 2000-2025, anomaly, Sen's slope
WITH {W}, y AS (SELECT w.code, w.area, left("Date",4)::int yr, sum("Daily_Surface_Runoff_Volume_m3_day")/6.5e9*1000 q FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE left("Date",4)::int BETWEEN 2000 AND 2025 GROUP BY 1,2,3),
a AS (SELECT yr, sum(area*q)/sum(area) v FROM y GROUP BY 1)
SELECT (SELECT round(v::numeric,1) FROM a WHERE yr=2000) q2000, (SELECT round(v::numeric,1) FROM a WHERE yr=2025) q2025, round(avg(v)::numeric,1) mean, round((((SELECT v FROM a WHERE yr=2025)-avg(v))/avg(v)*100)::numeric,1) anomaly_pct, round((((SELECT v FROM a WHERE yr=2000)-avg(v))/avg(v)*100)::numeric,1) anomaly_2000,
 (SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.v-a.v)/(b.yr-a.yr)) * 10)::numeric,1) FROM a a JOIN a b ON b.yr > a.yr) sen10,
 (SELECT string_agg(code||':'||round(((q25-mn)/mn*100)::numeric,1)||'('||round(mn::numeric,0)||')', ' ' ORDER BY code) FROM (SELECT code, max(q) FILTER (WHERE yr=2025) q25, avg(q) mn FROM y WHERE code IN ('CPT','DC34','DC44') GROUP BY 1) x) districts FROM a;;
-- F. hot days (4_1 Heatwave_Frequency_Days, one value per district-year) national 2000/2025/mean, Sen's slope; districts; cross-check vs count(Tmax > 35) in 2025
WITH {W}, y AS (SELECT w.code, w.area, left("Date",4)::int yr, max("Heatwave_Frequency_Days") h, count(DISTINCT "Heatwave_Frequency_Days") nd, count(*) FILTER (WHERE "Tmax_C" > 35) cnt FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE left("Date",4)::int BETWEEN 2000 AND 2025 GROUP BY 1,2,3),
a AS (SELECT yr, sum(area*h)/sum(area) v FROM y GROUP BY 1)
SELECT (SELECT round(v::numeric,1) FROM a WHERE yr=2000) h2000, (SELECT round(v::numeric,1) FROM a WHERE yr=2020) h2020, (SELECT round(v::numeric,1) FROM a WHERE yr=2025) h2025, round(avg(v)::numeric,2) mean, round(stddev_samp(v)::numeric,2) sd,
 (SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.v-a.v)/(b.yr-a.yr)) * 10)::numeric,2) FROM a a JOIN a b ON b.yr > a.yr) sen10, (SELECT max(nd) FROM y) distinct_per_year,
 (SELECT string_agg(code||':'||h||'/'||cnt, ' ' ORDER BY code) FROM y WHERE yr=2025 AND code IN ('CPT','DC34','DC44','DC45','DC5')) d2025_stored_vs_count, (SELECT count(*) FROM y WHERE h <> cnt) rows_where_stored_ne_count FROM a;;
-- G. frost nights: sum of Frost_Day_Flag per district-year, area-weighted; 2000, 2024, 2025
WITH {W}, y AS (SELECT w.code, w.area, left("Date",4)::int yr, sum("Frost_Day_Flag") f FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE left("Date",4)::int IN (2000, 2024, 2025) GROUP BY 1,2,3)
SELECT yr, round((sum(area*f)/sum(area))::numeric,1) frost FROM y GROUP BY 1 ORDER BY 1;;
-- H. drought frequency: % of area per SPI-3 class (D2/D3/D4) per month, averaged per year; district-months in D2+
WITH {W}, s AS (SELECT w.code, w.area, "Year" y, "Month" mo, round("SPI_3_Short_Term_Agricultural_Drought"::numeric,2) v FROM "6.2_rainfall_derived_drought_indices_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" IN (2000, 2019, 2020, 2025)),
c AS (SELECT *, CASE WHEN v <= -2.0 THEN 'D4' WHEN v <= -1.6 THEN 'D3' WHEN v <= -1.3 THEN 'D2' ELSE 'x' END cls FROM s),
m AS (SELECT y, mo, 100*sum(area) FILTER (WHERE cls='D2')/sum(area) d2, 100*sum(area) FILTER (WHERE cls='D3')/sum(area) d3, 100*sum(area) FILTER (WHERE cls='D4')/sum(area) d4, count(*) FILTER (WHERE cls<>'x') n FROM c GROUP BY 1,2)
SELECT y, round(avg(coalesce(d2,0))::numeric,1) d2, round(avg(coalesce(d3,0))::numeric,1) d3, round(avg(coalesce(d4,0))::numeric,1) d4, round(avg(coalesce(d2,0)+coalesce(d3,0)+coalesce(d4,0))::numeric,1) severe, sum(n) months, count(*) n_months FROM m GROUP BY 1 ORDER BY 1;;
-- I. indicator trends 2025 vs 2020: r20 % change (4_2)
WITH {W}, y AS (SELECT "Year" yr, sum(area*"R20mm_Days")/sum(area) r20 FROM "4_2_wet_dry_spells_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" IN (2020, 2021, 2025) GROUP BY 1)
SELECT round((((SELECT r20 FROM y WHERE yr=2025)-(SELECT r20 FROM y WHERE yr=2020))/(SELECT r20 FROM y WHERE yr=2020)*100)::numeric,1) r20_2025, round((((SELECT r20 FROM y WHERE yr=2021)-(SELECT r20 FROM y WHERE yr=2020))/(SELECT r20 FROM y WHERE yr=2020)*100)::numeric,1) r20_2021 FROM y LIMIT 1;;
-- J. temperature-long: Aug 2026 and Jan 2000 national monthly means of daily Tmax/Tavg/Tmin and the stored anomaly
WITH {W}, m AS (SELECT w.code, w.area, left("Date",7) ym, avg("Tmax_C") tx, avg("Tavg_C") ta, avg("Tmin_C") tn, max("Temperature_Anomaly_C") an FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE left("Date",7) IN ('2000-01','2026-07','2026-08') GROUP BY 1,2,3)
SELECT ym, round((sum(area*tx)/sum(area))::numeric,1) tmax, round((sum(area*ta)/sum(area))::numeric,1) tavg, round((sum(area*tn)/sum(area))::numeric,1) tmin, round((sum(area*an)/sum(area))::numeric,2) anomaly FROM m GROUP BY 1 ORDER BY 1;;
-- K. map-seasonality: decade means of 4_3 annual and seasonal totals (area-weighted per year, then mean of years)
WITH {W}, y AS (SELECT "Year" yr, sum(area*"MAP_mm")/sum(area) ann, sum(area*"Precip_DJF_mm")/sum(area) djf, sum(area*"Precip_MAM_mm")/sum(area) mam, sum(area*"Precip_JJA_mm")/sum(area) jja, sum(area*"Precip_SON_mm")/sum(area) son FROM "4_3_mean_annual_precipitation_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" BETWEEN 2000 AND 2025 GROUP BY 1)
SELECT CASE WHEN yr<2010 THEN '2000-2009' WHEN yr<2020 THEN '2010-2019' ELSE '2020-2025' END dec, round(avg(ann)::numeric,0) ann, round(avg(djf)::numeric,0) djf, round(avg(mam)::numeric,0) mam, round(avg(jja)::numeric,0) jja, round(avg(son)::numeric,0) son,
 round(avg(djf) FILTER (WHERE yr BETWEEN 2001 AND 2020)::numeric,0) p1_djf_check FROM y GROUP BY 1 ORDER BY 1;;
-- K2. seasonal-change rainfall: 2001-2020 and 2021-2025 means of the seasonal totals
WITH {W}, y AS (SELECT "Year" yr, sum(area*"Precip_DJF_mm")/sum(area) djf, sum(area*"Precip_MAM_mm")/sum(area) mam, sum(area*"Precip_JJA_mm")/sum(area) jja, sum(area*"Precip_SON_mm")/sum(area) son FROM "4_3_mean_annual_precipitation_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" BETWEEN 2001 AND 2025 GROUP BY 1)
SELECT CASE WHEN yr<=2020 THEN '2001-2020' ELSE '2021-2025' END p, round(avg(djf)::numeric,0) djf, round(avg(mam)::numeric,0) mam, round(avg(jja)::numeric,0) jja, round(avg(son)::numeric,0) son FROM y GROUP BY 1 ORDER BY 1;;
-- L. seasonal-change temperature: mean of the national monthly-mean Tavg over the season's months within each period (DJF = months 12,1,2 of the same calendar years)
WITH {W}, m AS (SELECT w.code, w.area, left("Date",7) ym, avg("Tavg_C") t FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE "Date" <= '2026-08-31' GROUP BY 1,2,3),
n AS (SELECT ym, left(ym,4)::int yr, right(ym,2)::int mo, sum(area*t)/sum(area) t FROM m GROUP BY 1)
SELECT CASE WHEN yr BETWEEN 2001 AND 2020 THEN 'p1' WHEN yr >= 2021 THEN 'p2' END p, round(avg(t) FILTER (WHERE mo IN (12,1,2))::numeric,2) djf, round(avg(t) FILTER (WHERE mo IN (3,4,5))::numeric,2) mam, round(avg(t) FILTER (WHERE mo IN (6,7,8))::numeric,2) jja, round(avg(t) FILTER (WHERE mo IN (9,10,11))::numeric,2) son FROM n WHERE yr >= 2001 GROUP BY 1 ORDER BY 1;;
-- M. provinces table: temp anomaly (12 months), rainfall change 2025 vs 2000-2025, hot days 2025, Sen's slope of annual Tavg; EC, NC, WC
WITH {W}, ta AS (SELECT w.code, w.prov, w.area, avg(a) a12 FROM (SELECT w.code, left("Date",7) ym, max("Temperature_Anomaly_C") a FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE left("Date",7) BETWEEN '2025-09' AND '2026-08' GROUP BY 1,2) x JOIN w ON w.code=x.code GROUP BY 1,2,3),
r AS (SELECT w.prov, "Year" yr, sum(area*"MAP_mm")/sum(area) v FROM "4_3_mean_annual_precipitation_master" t JOIN w ON w.name=t."District_Name" WHERE "Year" BETWEEN 2000 AND 2025 GROUP BY 1,2),
h AS (SELECT w.prov, sum(area*hh)/sum(area) hot FROM (SELECT w.code, max("Heatwave_Frequency_Days") hh FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE left("Date",4)='2025' GROUP BY 1) x JOIN w ON w.code=x.code GROUP BY 1),
ty AS (SELECT w.prov, left("Date",4)::int yr, sum(w.area*tt)/sum(w.area) t FROM (SELECT "District_Name" dn, left("Date",4) y4, avg("Tavg_C") tt FROM "4_1_temperature_change_master" WHERE left("Date",4)::int BETWEEN 2000 AND 2025 GROUP BY 1,2) x JOIN w ON w.name=x.dn JOIN "4_1_temperature_change_master" dummy ON false OR true GROUP BY 1,2 LIMIT 0)
SELECT ta.prov, round((sum(ta.area*a12)/sum(ta.area))::numeric,2) temp, (SELECT round(((max(v) FILTER (WHERE yr=2025)-avg(v))/avg(v)*100)::numeric,1) FROM r WHERE r.prov=ta.prov) rain, (SELECT round(hot::numeric,1) FROM h WHERE h.prov=ta.prov) hot FROM ta WHERE ta.prov IN ('EC','NC','WC','GP') GROUP BY 1 ORDER BY 1;;
-- M2. province Sen's slope of annual mean Tavg (EC, NC, WC, GP)
WITH {W}, d AS (SELECT w.prov, w.code, w.area, left("Date",4)::int yr, avg("Tavg_C") t FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE left("Date",4)::int BETWEEN 2000 AND 2025 AND w.prov IN ('EC','NC','WC','GP') GROUP BY 1,2,3,4),
y AS (SELECT prov, yr, sum(area*t)/sum(area) v FROM d GROUP BY 1,2)
SELECT a.prov, round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.v-a.v)/(b.yr-a.yr)) * 10)::numeric,2) sen10 FROM y a JOIN y b ON b.yr > a.yr AND b.prov=a.prov GROUP BY 1 ORDER BY 1;;
-- N. maps: cdd 2025 (4_2) and et0 2000-2025 mean per district (CPT, DC34, DC44); hot days 2025 DC45
WITH {W}, e AS (SELECT w.code, year yr, sum(et0_mm_day) et0 FROM meteorological_drought_master t JOIN w ON w.name=t.district_name WHERE year BETWEEN 2000 AND 2025 AND w.code IN ('CPT','DC34','DC44') GROUP BY 1,2)
SELECT code, round(avg(et0)::numeric,0) et0_mean, (SELECT "CDD_Days" FROM "4_2_wet_dry_spells_master" x JOIN w ON w.name=x."District_Name" WHERE w.code=e.code AND "Year"=2025) cdd2025 FROM e GROUP BY 1 ORDER BY 1;;
-- O. hot-days forecast history: district-month count of Tmax > 35, area-weighted, Nov 2025 - Feb 2026 (and the national sum of district-days for comparison)
WITH {W}, m AS (SELECT w.code, w.area, left("Date",7) ym, count(*) FILTER (WHERE "Tmax_C" > 35) c FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE left("Date",7) BETWEEN '2025-09' AND '2026-08' GROUP BY 1,2,3)
SELECT ym, round((sum(area*c)/sum(area))::numeric,2) weighted_mean, sum(c) district_days FROM m GROUP BY 1 ORDER BY 1;;
-- P. the DB's temperature anomaly baseline check: stored anomaly = Tavg monthly mean - 2000-2026 same-month mean?
WITH {W}, m AS (SELECT w.code, left("Date",7) ym, avg("Tavg_C") t, max("Temperature_Anomaly_C") a FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE w.code='CPT' GROUP BY 1,2),
c AS (SELECT right(ym,2) mo, avg(t) tn FROM m GROUP BY 1)
SELECT round(max(abs(a - (t - tn)))::numeric,3) max_abs_diff_vs_2000_2026_baseline, count(*) n FROM m JOIN c ON c.mo=right(m.ym,2);;
