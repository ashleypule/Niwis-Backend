-- A. KPIs Dec 2025: TCI, LST (area-weighted), NDVI anomaly vs Dec 2001-2025 (national series), districts
WITH {W}, s AS (SELECT w.code, w.area, date, ndvi, tci, lst, vci, vhi, ndwi, surface_soil_moisture sm FROM satellite_remote_sensing t JOIN w ON w.name=t.district_name),
n AS (SELECT date, sum(area*ndvi)/sum(area) ndvi, sum(area*tci)/sum(area) tci, sum(area*lst)/sum(area) lst, sum(area*vci)/sum(area) vci, sum(area*vhi)/sum(area) vhi, sum(area*ndwi)/sum(area) ndwi, sum(area*sm)/sum(area) sm, count(*) n FROM s GROUP BY 1),
dn AS (SELECT code, extract(month from date) mo, avg(ndvi) nn FROM s WHERE extract(year from date) BETWEEN 2001 AND 2025 GROUP BY 1,2)
SELECT (SELECT round(tci::numeric,1)||'/'||round(lst::numeric,1)||'/'||round(ndwi::numeric,3) FROM n WHERE date='2025-12-01') tci_lst_ndwi_dec25,
 round((((SELECT ndvi FROM n WHERE date='2025-12-01') / (SELECT avg(ndvi) FROM n WHERE extract(month from date)=12 AND extract(year from date) BETWEEN 2001 AND 2025)) * 100 - 100)::numeric,1) ndvi_anom_dec25,
 (SELECT string_agg(to_char(date,'YYYY-MM')||':'||round((ndvi/(SELECT avg(ndvi) FROM n n2 WHERE extract(month from n2.date)=extract(month from n.date) AND extract(year from n2.date) BETWEEN 2001 AND 2025)*100-100)::numeric,1), ' ' ORDER BY date) FROM n WHERE date BETWEEN '2025-09-01' AND '2025-12-01') anom_series,
 (SELECT string_agg(code||':'||round(tci::numeric,1)||'/'||round(lst::numeric,1)||'/'||round(((ndvi-nn)/nn*100)::numeric,1)||'/'||round((ndvi-nn)::numeric,3)||'/'||round(ndwi::numeric,3), ' ' ORDER BY code) FROM s JOIN dn ON dn.code=s.code AND dn.mo=12 WHERE date='2025-12-01' AND s.code IN ('CPT','DC34','DC44','DC6','DC3')) d_tci_lst_anompct_anomabs_ndwi,
 (SELECT round((avg(ndvi)-stddev_samp(ndvi))::numeric,3)||'-'||round((avg(ndvi)+stddev_samp(ndvi))::numeric,3) FROM n WHERE extract(month from date)=12 AND extract(year from date) BETWEEN 2001 AND 2025) dec_band_ndvi,
 (SELECT round((avg(vci)-stddev_samp(vci))::numeric,1)||'-'||round((avg(vci)+stddev_samp(vci))::numeric,1) FROM n WHERE extract(month from date)=12 AND extract(year from date) BETWEEN 2001 AND 2025) dec_band_vci,
 (SELECT round(ndvi::numeric,3) FROM n WHERE date='2024-12-01') ndvi_dec24, (SELECT round(vci::numeric,1) FROM n WHERE date='2024-12-01') vci_dec24;;
-- B. classification by VHI class (area shares) national and per province, Dec 2025
WITH {W}, s AS (SELECT w.code, w.prov, w.area, vhi, CASE WHEN vhi >= 80 THEN 'Excellent' WHEN vhi >= 60 THEN 'Good' WHEN vhi >= 40 THEN 'Fair' WHEN vhi >= 20 THEN 'Poor' ELSE 'Very Poor' END cls FROM satellite_remote_sensing t JOIN w ON w.name=t.district_name WHERE date='2025-12-01')
SELECT coalesce(prov,'ZA') prov, string_agg(cls||'='||pct, ' ' ORDER BY cls) FROM (SELECT prov, cls, round((100*sum(area)/sum(sum(area)) OVER (PARTITION BY prov))::numeric,1) pct FROM s GROUP BY GROUPING SETS ((prov, cls), (cls))) x GROUP BY 1 ORDER BY 1;;
-- C. seasonal mean NDVI of the national series: DJF 2025 (Dec 2024 + Jan, Feb 2025), MAM 2025, JJA 2025, SON 2025, DJF 2019
WITH {W}, n AS (SELECT date, sum(area*ndvi)/sum(area) v FROM satellite_remote_sensing t JOIN w ON w.name=t.district_name GROUP BY 1)
SELECT round(avg(v) FILTER (WHERE date IN ('2024-12-01','2025-01-01','2025-02-01'))::numeric,3) djf25, round(avg(v) FILTER (WHERE date IN ('2025-03-01','2025-04-01','2025-05-01'))::numeric,3) mam25, round(avg(v) FILTER (WHERE date IN ('2025-06-01','2025-07-01','2025-08-01'))::numeric,3) jja25, round(avg(v) FILTER (WHERE date IN ('2025-09-01','2025-10-01','2025-11-01'))::numeric,3) son25, round(avg(v) FILTER (WHERE date IN ('2018-12-01','2019-01-01','2019-02-01'))::numeric,3) djf19 FROM n;;
-- D. provinces table Dec 2025: NDVI, VCI, change vs Dec 2001-2025 mean of the province series (FS, WC, EC); hotspots: DC3 VCI and NDVI change
WITH {W}, s AS (SELECT w.code, w.prov, w.area, date, ndvi, vci FROM satellite_remote_sensing t JOIN w ON w.name=t.district_name),
p AS (SELECT prov, date, sum(area*ndvi)/sum(area) ndvi, sum(area*vci)/sum(area) vci FROM s GROUP BY 1,2)
SELECT prov, round(ndvi::numeric,3) ndvi, round(vci::numeric,1) vci, round(((ndvi/(SELECT avg(ndvi) FROM p p2 WHERE p2.prov=p.prov AND extract(month from p2.date)=12 AND extract(year from p2.date) BETWEEN 2001 AND 2025))*100-100)::numeric,1) change FROM p WHERE date='2025-12-01' AND prov IN ('FS','WC','EC','NW') ORDER BY 1;;
WITH {W} SELECT w.code, round(vci::numeric,1) vci, round((ndvi - (SELECT avg(ndvi) FROM satellite_remote_sensing x WHERE x.district_name=t.district_name AND extract(month from x.date)=12 AND extract(year from x.date) BETWEEN 2001 AND 2025))::numeric,3) ndvi_change FROM satellite_remote_sensing t JOIN w ON w.name=t.district_name WHERE date='2025-12-01' ORDER BY vci LIMIT 4;;
-- E. drivers breakdown: last 6 complete months vs the same months' 2001-2025 mean. Rainfall % (Mar-Aug 2026), mean temperature C, ET0 % (daily means), GDD (5.2 monthly), LST C (Jul-Dec 2025)
WITH {W}, r AS (SELECT w.code, w.area, "Year" y, "Month" mo, sum("Daily_Rainfall_Total_mm") p FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Month" BETWEEN 3 AND 8 AND "Year" BETWEEN 2001 AND 2026 GROUP BY 1,2,3,4),
rn AS (SELECT y, mo, sum(area*p)/sum(area) p FROM r GROUP BY 1,2),
t AS (SELECT w.code, w.area, left("Date",4)::int y, substr("Date",6,2)::int mo, avg("Tavg_C") tt FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE substr("Date",6,2)::int BETWEEN 3 AND 8 AND left("Date",4)::int BETWEEN 2001 AND 2026 GROUP BY 1,2,3,4),
tn AS (SELECT y, mo, sum(area*tt)/sum(area) tt FROM t GROUP BY 1,2),
e AS (SELECT w.code, w.area, year y, month mo, sum(et0_mm_day) e FROM meteorological_drought_master t JOIN w ON w.name=t.district_name WHERE month BETWEEN 3 AND 8 AND year BETWEEN 2001 AND 2026 GROUP BY 1,2,3,4),
en AS (SELECT y, mo, sum(area*e)/sum(area) e FROM e GROUP BY 1,2)
SELECT round(((SELECT sum(p) FROM rn WHERE y=2026) / (SELECT sum(p)/25 FROM rn WHERE y BETWEEN 2001 AND 2025) * 100 - 100)::numeric,1) rain_pct,
 round(((SELECT avg(tt) FROM tn WHERE y=2026) - (SELECT avg(tt) FROM tn WHERE y BETWEEN 2001 AND 2025))::numeric,2) temp_c,
 round(((SELECT sum(e) FROM en WHERE y=2026) / (SELECT sum(e)/25 FROM en WHERE y BETWEEN 2001 AND 2025) * 100 - 100)::numeric,1) et0_pct;;
WITH {W}, l AS (SELECT w.code, w.area, date, lst FROM satellite_remote_sensing t JOIN w ON w.name=t.district_name WHERE extract(month from date) BETWEEN 7 AND 12),
n AS (SELECT date, sum(area*lst)/sum(area) v FROM l GROUP BY 1)
SELECT round(((SELECT avg(v) FROM n WHERE date >= '2025-07-01') - (SELECT avg(v) FROM n WHERE extract(year from date) BETWEEN 2001 AND 2025))::numeric,2) lst_c, (SELECT count(*) FROM n WHERE date >= '2025-07-01') n_cur;;
-- E2. GDD (5.2) column names and the 6-month change
SELECT column_name, data_type FROM information_schema.columns WHERE table_name='5.2_meteorological_drivers_saws_master' ORDER BY ordinal_position;;
-- F. NDVI trend per district: Sen's slope per decade of annual mean NDVI 2001-2025 (CPT, DC34, DC44); drivers timeseries Aug 2026 Tmax mean, ET0 mean daily
WITH {W}, y AS (SELECT w.code, extract(year from date)::int yr, avg(ndvi) v FROM satellite_remote_sensing t JOIN w ON w.name=t.district_name WHERE extract(year from date) BETWEEN 2001 AND 2025 AND w.code IN ('CPT','DC34','DC44') GROUP BY 1,2)
SELECT a.code, round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.v-a.v)/(b.yr-a.yr)) * 10)::numeric,4) sen10 FROM y a JOIN y b ON b.yr > a.yr AND b.code=a.code GROUP BY 1 ORDER BY 1;;
WITH {W}, m AS (SELECT w.code, w.area, avg("Tmax_C") tx FROM "4_1_temperature_change_master" t JOIN w ON w.name=t."District_Name" WHERE "Date" LIKE '2026-08-%' GROUP BY 1,2), e AS (SELECT w.code, w.area, avg(et0_mm_day) e FROM meteorological_drought_master t JOIN w ON w.name=t.district_name WHERE date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY 1,2)
SELECT round((sum(m.area*tx)/sum(m.area))::numeric,1) tmax_aug26, (SELECT round((sum(area*e)/sum(area))::numeric,2) FROM e) et0_daily_aug26 FROM m;;
