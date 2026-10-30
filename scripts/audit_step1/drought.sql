-- A. SPI-6 Aug 2026: class areas (spec 3.4: round 2dp then <=), national shares, headline class, severity km2
WITH {W}, s AS (SELECT w.code, w.prov, w.area, round("SPI_6_Medium_Term_Hydrological_Impacts"::numeric,2) v FROM "6.2_rainfall_derived_drought_indices_master" t JOIN w ON w.name=t."District_Name" WHERE "Year"=2026 AND "Month"=8),
c AS (SELECT *, CASE WHEN v <= -2.0 THEN 'D4' WHEN v <= -1.6 THEN 'D3' WHEN v <= -1.3 THEN 'D2' WHEN v <= -0.8 THEN 'D1' WHEN v <= -0.5 THEN 'D0' ELSE 'None' END cls FROM s)
SELECT cls, round(sum(area)::numeric,0) km2, round((100*sum(area)/(SELECT sum(area) FROM c))::numeric,2) pct, count(*) n FROM c GROUP BY 1 ORDER BY 1;;
-- A2. province shares in D1-D4 and the area-rule class per province (cumulative worst class >= 20 %)
WITH {W}, s AS (SELECT w.code, w.prov, w.area, round("SPI_6_Medium_Term_Hydrological_Impacts"::numeric,2) v FROM "6.2_rainfall_derived_drought_indices_master" t JOIN w ON w.name=t."District_Name" WHERE "Year"=2026 AND "Month"=8),
c AS (SELECT *, CASE WHEN v <= -2.0 THEN 5 WHEN v <= -1.6 THEN 4 WHEN v <= -1.3 THEN 3 WHEN v <= -0.8 THEN 2 WHEN v <= -0.5 THEN 1 ELSE 0 END r FROM s),
p AS (SELECT prov, sum(area) tot, sum(area) FILTER (WHERE r>=2) ind, round((sum(area*v)/sum(area))::numeric,2) spi6 FROM c GROUP BY 1),
cum AS (SELECT c.prov, c.r, sum(area) OVER (PARTITION BY c.prov ORDER BY c.r DESC) / p.tot * 100 cumpct FROM c JOIN p USING (prov))
SELECT p.prov, p.spi6, round((100*ind/tot)::numeric,1) in_drought_pct, coalesce((SELECT max(r) FROM cum WHERE cum.prov=p.prov AND cumpct >= 20),0) area_rule_rank FROM p ORDER BY spi6;;
-- B. drought-area series (SPI-6 classes) Sep 2025 and Aug 2026, SPI-3 Aug 2026, SPI-1 Aug 2026, SPI-12 Sep 2025
WITH {W}, s AS (SELECT w.code, w.area, "Year" y, "Month" mo, round("SPI_6_Medium_Term_Hydrological_Impacts"::numeric,2) v6, round("SPI_3_Short_Term_Agricultural_Drought"::numeric,2) v3, round("SPI_1_Meteorological_Drought_Onset"::numeric,2) v1, round("SPI_12_Long_Term_Groundwater_Impacts"::numeric,2) v12, round("SPI_24_Very_Long_Term_Drought"::numeric,2) v24 FROM "6.2_rainfall_derived_drought_indices_master" t JOIN w ON w.name=t."District_Name" WHERE ("Year"=2026 AND "Month"=8) OR ("Year"=2025 AND "Month"=9)),
f AS (SELECT y, mo, 'spi6' k, v6 v, area FROM s UNION ALL SELECT y, mo, 'spi3', v3, area FROM s UNION ALL SELECT y, mo, 'spi1', v1, area FROM s UNION ALL SELECT y, mo, 'spi12', v12, area FROM s UNION ALL SELECT y, mo, 'spi24', v24, area FROM s),
c AS (SELECT y, mo, k, area, CASE WHEN v <= -2.0 THEN 'D4' WHEN v <= -1.6 THEN 'D3' WHEN v <= -1.3 THEN 'D2' WHEN v <= -0.8 THEN 'D1' WHEN v <= -0.5 THEN 'D0' ELSE 'None' END cls FROM f)
SELECT y, mo, k, string_agg(cls || '=' || pct, ' ' ORDER BY cls) FROM (SELECT y, mo, k, cls, round((100*sum(area)/1222435.4)::numeric,1) pct FROM c GROUP BY 1,2,3,4) x GROUP BY 1,2,3 ORDER BY 1,2,3;;
-- C. satellite: VCI, VHI, NDVI, soil moisture Dec 2025 national (area-weighted) + per district; stress area share (VHI<40)
WITH {W}, s AS (SELECT w.code, w.area, vci, vhi, ndvi, surface_soil_moisture sm, rootzone_soil_moisture rz FROM satellite_remote_sensing t JOIN w ON w.name=t.district_name WHERE date='2025-12-01')
SELECT round((sum(area*vci)/sum(area))::numeric,1) vci, round((sum(area*vhi)/sum(area))::numeric,1) vhi, round((sum(area*ndvi)/sum(area))::numeric,3) ndvi, round((sum(area*sm)/sum(area))::numeric,3) sm, round((sum(area*rz)/sum(area))::numeric,3) rz, count(*) n,
 round((100*coalesce(sum(area) FILTER (WHERE vhi < 40),0)/sum(area))::numeric,1) stress_pct,
 (SELECT string_agg(code||':'||round(vci::numeric,1)||'/'||round(ndvi::numeric,3)||'/'||round(sm::numeric,3), ' ' ORDER BY code) FROM s WHERE code IN ('DC44','CPT','DC34','DC25')) d_vci_ndvi_sm FROM s;;
-- C2. soil-moisture anomaly Dec 2025 vs Dec mean 2015-2025 (scope: weighted current vs weighted climatology), and the national series range/mean for Dec and Sep over 2015-2025
WITH {W}, m AS (SELECT date, sum(area*surface_soil_moisture)/sum(area) v, count(*) n FROM satellite_remote_sensing t JOIN w ON w.name=t.district_name WHERE date >= '2015-03-01' GROUP BY 1)
SELECT (SELECT round(v::numeric,4) FROM m WHERE date='2025-12-01') dec25, round(avg(v) FILTER (WHERE extract(month from date)=12 AND extract(year from date) BETWEEN 2015 AND 2025)::numeric,4) dec_mean,
  round(((SELECT v FROM m WHERE date='2025-12-01') / avg(v) FILTER (WHERE extract(month from date)=12 AND extract(year from date) BETWEEN 2015 AND 2025) * 100 - 100)::numeric,1) anomaly_pct,
  round(min(v) FILTER (WHERE extract(month from date)=12 AND extract(year from date) BETWEEN 2015 AND 2025)::numeric,3) dec_min, round(max(v) FILTER (WHERE extract(month from date)=12 AND extract(year from date) BETWEEN 2015 AND 2025)::numeric,3) dec_max,
  round(min(v) FILTER (WHERE extract(month from date)=9 AND extract(year from date) BETWEEN 2015 AND 2025)::numeric,3) sep_min, round(max(v) FILTER (WHERE extract(month from date)=9 AND extract(year from date) BETWEEN 2015 AND 2025)::numeric,3) sep_max, round(avg(v) FILTER (WHERE extract(month from date)=9 AND extract(year from date) BETWEEN 2015 AND 2025)::numeric,3) sep_mean,
  min(n) min_n, (SELECT min(date) FROM satellite_remote_sensing) first_sat, (SELECT max(date) FROM satellite_remote_sensing) last_sat FROM m;;
-- C3. soil-moisture: is the pre-Mar-2015 fill still constant? distinct values before/after
SELECT count(DISTINCT surface_soil_moisture) FILTER (WHERE date < '2015-03-01') before, count(DISTINCT surface_soil_moisture) FILTER (WHERE date >= '2015-03-01') after FROM satellite_remote_sensing;;
-- D. SGI (hydrological_drought_master) national Sep 2023 / Aug 2024 and the district anomaly check
WITH {W}, s AS (SELECT w.code, w.area, date, sgi_dimensionless v FROM hydrological_drought_master t JOIN w ON w.name=t.district_name WHERE date IN ('2023-09-01','2024-08-01','2024-09-01','2025-12-01'))
SELECT date, round((sum(area*v)/sum(area))::numeric,2) sgi, count(*) n FROM s GROUP BY 1 ORDER BY 1;;
-- E. SPEI-3 Aug 2026 national (meteorological)
WITH {W}, e AS (SELECT w.code, w.area, max(spei_3) v FROM meteorological_drought_master t JOIN w ON w.name=t.district_name WHERE date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY 1,2)
SELECT round((sum(area*v)/sum(area))::numeric,2) spei3 FROM e;;
-- F. veg heatmap: VCI by province Dec 2025 and Jan 2025
WITH {W}, s AS (SELECT w.prov, w.area, date, vci FROM satellite_remote_sensing t JOIN w ON w.name=t.district_name WHERE date IN ('2025-01-01','2025-12-01'))
SELECT prov, string_agg(to_char(date,'YYYY-MM')||'='||v, ' ' ORDER BY date) FROM (SELECT prov, date, round((sum(area*vci)/sum(area))::numeric,1) v FROM s GROUP BY 1,2) x GROUP BY 1 ORDER BY 1;;
-- G. municipalities table: lowest SPI-6 Aug 2026
WITH {W} SELECT w.code, round("SPI_6_Medium_Term_Hydrological_Impacts"::numeric,2) v FROM "6.2_rainfall_derived_drought_indices_master" t JOIN w ON w.name=t."District_Name" WHERE "Year"=2026 AND "Month"=8 ORDER BY 2 LIMIT 6;;
-- H. maps: spi24 / spei3 / spei12 Aug 2026 for DC44, DC32, DC34; rain-anomaly 3 months (2026-06-07..2026-09-06) CPT, DC1, BUF
WITH {W}, s AS (SELECT w.code, round("SPI_24_Very_Long_Term_Drought"::numeric,2) spi24, round("SPEI_12_Annual_Water_Balance_Drought"::numeric,2) spei12_62, round("SPEI_3_Agricultural_Drought_ET"::numeric,2) spei3_62 FROM "6.2_rainfall_derived_drought_indices_master" t JOIN w ON w.name=t."District_Name" WHERE "Year"=2026 AND "Month"=8),
e AS (SELECT w.code, round(max(spei_3)::numeric,2) spei3, round(max(spei_12)::numeric,2) spei12 FROM meteorological_drought_master t JOIN w ON w.name=t.district_name WHERE date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY 1)
SELECT s.code, spi24, e.spei3, e.spei12, spei3_62, spei12_62 FROM s JOIN e USING (code) WHERE s.code IN ('DC44','DC32','DC34','DC23') ORDER BY 1;;
WITH {W}, d AS (SELECT w.code, sum("Daily_Rainfall_Total_mm") s, count(*) n FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2026-06-07' AND '2026-09-06' GROUP BY 1),
nm AS (SELECT w.code, "Month" mo, max("Monthly_Total_Precipitation_mm" - "Monthly_Anomaly_mm") n FROM "6.1_core_rainfall_variables_master" t JOIN w ON w.name=t."District_Name" GROUP BY 1,2),
days AS (SELECT d::date d FROM generate_series('2026-06-07'::date,'2026-09-06'::date, interval '1 day') d),
frac AS (SELECT extract(month from d) mo, sum(1.0/extract(day from (date_trunc('month', d) + interval '1 month - 1 day'))) f FROM days GROUP BY 1),
nw AS (SELECT nm.code, sum(nm.n * frac.f) n FROM nm JOIN frac ON frac.mo = nm.mo GROUP BY 1)
SELECT d.code, round(((d.s-nw.n)/nw.n*100)::numeric,1) anom3, d.n FROM d JOIN nw USING (code) WHERE d.code IN ('CPT','DC1','BUF','DC44','DC34') ORDER BY 1;;
-- I. district KPIs: SPI-3/6, SPEI-12 Aug 2026; VCI / soil moisture anomaly Dec 2025 for CPT, DC34, DC44
WITH {W}, s AS (SELECT w.code, round("SPI_3_Short_Term_Agricultural_Drought"::numeric,2) spi3, round("SPI_6_Medium_Term_Hydrological_Impacts"::numeric,2) spi6 FROM "6.2_rainfall_derived_drought_indices_master" t JOIN w ON w.name=t."District_Name" WHERE "Year"=2026 AND "Month"=8),
e AS (SELECT w.code, round(max(spei_12)::numeric,2) spei12 FROM meteorological_drought_master t JOIN w ON w.name=t.district_name WHERE date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY 1),
v AS (SELECT w.code, round(vci::numeric,1) vci, surface_soil_moisture sm FROM satellite_remote_sensing t JOIN w ON w.name=t.district_name WHERE date='2025-12-01'),
cl AS (SELECT w.code, avg(surface_soil_moisture) n FROM satellite_remote_sensing t JOIN w ON w.name=t.district_name WHERE extract(month from date)=12 AND extract(year from date) BETWEEN 2015 AND 2025 GROUP BY 1)
SELECT s.code, spi3, spi6, spei12, vci, round(((v.sm-cl.n)/cl.n*100)::numeric,1) sm_anom FROM s JOIN e USING (code) JOIN v USING (code) JOIN cl USING (code) WHERE s.code IN ('CPT','DC34','DC44') ORDER BY 1;;
-- J. monthly runoff depth (mm) national area-weighted, 2000-01..2026-08 (for the SSI-3 recomputation) -> csv-ish
WITH {W}, m AS (SELECT w.code, w.area, date_trunc('month', "Date")::date mth, sum("Daily_Surface_Runoff_Volume_m3_day")/6.5e9*1000 depth, count(*) n FROM runoff_variables_master t JOIN w ON w.name=t."District_Name" WHERE "Date" BETWEEN '2000-01-01' AND '2026-08-31' GROUP BY 1,2,3)
SELECT string_agg(to_char(mth,'YYYY-MM')||':'||round(v::numeric,4), ',' ORDER BY mth) FROM (SELECT mth, sum(area*depth)/sum(area) v, count(*) n FROM m GROUP BY 1 HAVING count(*)=52) x;;
-- K. newest dates of the drought tables; future rows?
SELECT (SELECT max(date) FROM satellite_remote_sensing) sat, (SELECT max(date) FROM hydrological_drought_master) hyd, (SELECT max("Date") FROM runoff_variables_master) runoff, (SELECT max(date) FROM "9_1_hydrological_station_master") sw, (SELECT max(date) FROM "10_1_borehole_and_aquifer_master") gw, (SELECT max(date) FROM "10_1_borehole_and_aquifer_master" WHERE date <= CURRENT_DATE) gw_notfuture;;
