-- PROPOSED — for review by the NIWIS data team. The backend team does NOT run this.
--
-- Why: the API currently connects as `postgres` (full privileges) because no read-only login has SELECT on the
-- NIWIS tables (`supabase_read_only_user` exists but has no grants on them). The API only needs SELECT on the
-- tables below. A dedicated login limits the damage if the API's credentials leak.
--
-- After creating it, give the backend the connection string for `niwis_api_ro` via the session pooler
-- (user `niwis_api_ro.<project-ref>`); the backend only changes SUPABASE_DB_URL.
--
-- RLS: all 14 tables have row-level security enabled with no policies. Table owners and roles with BYPASSRLS see
-- all rows; a normal role sees none. Either grant BYPASSRLS to this role (below), or add a SELECT policy per table
-- (`CREATE POLICY api_read ON <table> FOR SELECT TO niwis_api_ro USING (true);`).

CREATE ROLE niwis_api_ro WITH LOGIN PASSWORD '<choose a strong password>' NOSUPERUSER NOCREATEDB NOCREATEROLE
  BYPASSRLS;  -- or omit BYPASSRLS and add per-table SELECT policies instead
ALTER ROLE niwis_api_ro SET default_transaction_read_only = on;
ALTER ROLE niwis_api_ro SET statement_timeout = '10s';

GRANT USAGE ON SCHEMA public TO niwis_api_ro;
GRANT SELECT ON
  public.meteorological_drought_master,
  public.satellite_remote_sensing,
  public.hydrological_drought_master,
  public."4_1_temperature_change_master",
  public."4_2_wet_dry_spells_master",
  public."4_3_mean_annual_precipitation_master",
  public."4_4_potential_evaporation_daily_master",
  public."4_5_irrigation_demand_daily_master",
  public."4_6_streamflow_and_climate_linkage_annual _master",
  public."5.1_primary_satellite_vegetation_variables_sansa_master",
  public."5.2_meteorological_drivers_saws_master",
  public."6.1_core_rainfall_variables_master",
  public."6.2_rainfall_derived_drought_indices_master",
  public.runoff_variables_master
TO niwis_api_ro;

-- The meta endpoints read table sizes and index lists from the catalogue (pg_class, pg_indexes); these are
-- readable by any role, so no extra grants are needed.
