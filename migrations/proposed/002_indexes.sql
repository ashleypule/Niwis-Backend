-- PROPOSED — for review by the NIWIS data team. The backend team does NOT run this.
--
-- Why: ten of the fourteen tables have no primary key and no index (see docs/DB_INVENTORY.md). Every API query
-- filters by district and a date range, so without an index each request scans the whole table (~500 000 rows,
-- 50–100 MB for the daily tables). These indexes make district + period lookups index scans.
--
-- `CREATE INDEX CONCURRENTLY` avoids locking the tables while data is still being loaded; it cannot run inside a
-- transaction block, so run the statements one at a time.
--
-- Unique indexes double as a data-quality guard (one row per district per day/month/year). The inventory found no
-- duplicate (district, date) pairs today, so they should build without errors.

-- Daily tables keyed by a text "Date" column (ISO yyyy-mm-dd, so text order = date order)
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_4_1_district_date ON public."4_1_temperature_change_master" ("District_Name", "Date");
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_4_4_district_date ON public."4_4_potential_evaporation_daily_master" ("District_Name", "Date");
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_4_5_district_date ON public."4_5_irrigation_demand_daily_master" ("District_Name", "Date");
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_5_1_district_date ON public."5.1_primary_satellite_vegetation_variables_sansa_master" ("District_Name", "Date");
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_6_1_district_date ON public."6.1_core_rainfall_variables_master" ("District_Name", "Date");

-- Monthly-by-(Year, Month) daily tables: the API reads one row per district-month for repeated monthly columns
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_6_1_district_year_month ON public."6.1_core_rainfall_variables_master" ("District_Name", "Year", "Month");
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_5_1_district_year_month ON public."5.1_primary_satellite_vegetation_variables_sansa_master" ("District_Name", "Year", "Month");
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_met_district_year_month ON public.meteorological_drought_master (district_name, year, month);

-- Monthly tables
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_5_2_district_year_month ON public."5.2_meteorological_drivers_saws_master" ("District_Name", "Year", "Month");
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_6_2_district_year_month ON public."6.2_rainfall_derived_drought_indices_master" ("District_Name", "Year", "Month");

-- Annual tables (small — 1 404 rows — mainly a uniqueness guard)
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_4_2_district_year ON public."4_2_wet_dry_spells_master" ("District_Name", "Year");
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_4_3_district_year ON public."4_3_mean_annual_precipitation_master" ("District_Name", "Year");
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_4_6_district_year ON public."4_6_streamflow_and_climate_linkage_annual _master" ("District_Name", "Year");

-- Already indexed (no action): meteorological_drought_master, satellite_remote_sensing,
-- hydrological_drought_master (unique (district_name, date)) and runoff_variables_master (PK (District_Name, Date)).
