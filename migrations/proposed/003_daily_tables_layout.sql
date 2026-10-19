-- PROPOSED — for review by the NIWIS data team. The backend team does NOT run this.
--
-- Why (frontend BACKEND_ISSUES #14, 2026-10-19): the (district, date) indexes the data team created make district-scoped
-- requests use index scans, but each district's rows are spread over the whole table (the daily tables are stored in
-- load order, i.e. by date), so a scoped query still touches almost every page. On a cold cache Supabase reads ≈ 0.64 ms
-- per page (EXPLAIN ANALYZE: 10 484 pages read from disk in 6.7 s), which is how scoped seasonal totals reached the 10 s
-- statement timeout.
--
-- The API now reads monthly / annual values that repeat on every daily row from the first day of each period only (one
-- row per district-period instead of every day). That alone fixes #14. What remains slow on a cold cache is a long daily
-- series for a scope (e.g. a district's whole daily record): ≈ 9 800 pages, ≈ 6 s. Either option below fixes it.
--
-- Measured on an exact copy of 6.1 (506 792 rows, 94 MB, same index; pages touched per query, EXPLAIN (ANALYZE,
-- BUFFERS); times on a warm local copy):
--
--   query                                                   current index   option A: CLUSTER   option B: covering
--   seasonal totals, Western Cape, every daily row (old)       12 009 pages       1 674 pages          632 pages
--   seasonal totals, Western Cape, first day per year (new)       520 pages         624 pages          469 pages
--   daily rainfall, Ehlanzeni (DC32), whole record              9 797 pages         279 pages          106 pages
--   daily rainfall, Western Cape, 12 months                       479 pages          86 pages           42 pages
--
-- At ≈ 0.64 ms per cold page, a district's whole daily record goes from ≈ 6.3 s to ≈ 0.2 s (A) or ≈ 0.07 s (B).

-- ---------------------------------------------------------------- option A (recommended): store each district's rows together
-- One-off physical reorder on the existing (district, date) indexes. CLUSTER takes an ACCESS EXCLUSIVE lock and
-- rewrites the table (≈ 1 min each): run it in a quiet window. Rows loaded later are appended in load order again, so
-- re-run it after each bulk reload (or monthly). Helps every column of the table, no extra index to maintain.
CLUSTER public."6.1_core_rainfall_variables_master" USING idx_rainfall_district_date;
CLUSTER public."4_1_temperature_change_master" USING idx_temperature_change_district_date;
CLUSTER public.meteorological_drought_master USING unique_district_date;
CLUSTER public.runoff_variables_master USING runoff_variables_master_pkey;
ANALYZE public."6.1_core_rainfall_variables_master";
ANALYZE public."4_1_temperature_change_master";
ANALYZE public.meteorological_drought_master;
ANALYZE public.runoff_variables_master;

-- ---------------------------------------------------------------- option B: covering index for the most-read 6.1 columns
-- Index-only scans for the rainfall page's daily and seasonal charts, with no lock on the table (CONCURRENTLY) and
-- nothing to re-run after loads; costs ≈ 30 MB and only covers the listed columns. Index-only scans need the
-- visibility map, which autovacuum keeps current.
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_6_1_district_date_cover ON public."6.1_core_rainfall_variables_master"
--     ("District_Name", "Date") INCLUDE ("Daily_Rainfall_Total_mm", "Seasonal_Total_JFM_mm", "Seasonal_Total_AMJ_mm",
--     "Seasonal_Total_JAS_mm", "Seasonal_Total_OND_mm");
