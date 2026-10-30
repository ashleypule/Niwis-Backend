-- PROPOSED — run by the NIWIS data team (a database owner), never by the API. Generated from the dataset registry by
-- scripts/gen_readonly_role.py: regenerate it whenever a table is registered.
--
-- Creates `niwis_api_ro`, a login that can only SELECT the 44 tables the API reads. The API then connects with it
-- instead of `postgres` (the API also forces every session READ ONLY; this role makes that true at the database
-- level, and limits the damage if the API's credentials leak).
--
-- Run (psql ≥ 10; the password never appears in the file or the shell history if you type it at the prompt):
--
--   psql "<owner connection string>" -v ON_ERROR_STOP=1 \
--        -v ro_password="$(read -rsp 'niwis_api_ro password: ' p; echo "$p")" \
--        -f migrations/proposed/001_readonly_api_role.sql
--
-- It is idempotent (re-running updates the password and re-applies the grants) and runs in one transaction.
-- Afterwards: SUPABASE_DB_URL = the session-pooler string for user `niwis_api_ro.<project-ref>`, then
-- DB_READONLY_ROLE_CONFIRMED=true (ENV=production refuses to start without it). See DEPLOYMENT.md.
--
-- Row-level security: every public table has RLS enabled and no policies, so a normal role sees no rows. This script
-- adds one SELECT-only policy per table for this role (USING (true): the data are public). The alternative is
-- `ALTER ROLE niwis_api_ro BYPASSRLS` instead of the policies — simpler, but it bypasses any policy added later.

\set ON_ERROR_STOP on
BEGIN;

SELECT format('CREATE ROLE niwis_api_ro LOGIN PASSWORD %L', :'ro_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'niwis_api_ro') \gexec
SELECT format('ALTER ROLE niwis_api_ro LOGIN PASSWORD %L', :'ro_password') \gexec

ALTER ROLE niwis_api_ro NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT 20;
ALTER ROLE niwis_api_ro SET default_transaction_read_only = on;
ALTER ROLE niwis_api_ro SET statement_timeout = '60s';  -- the API sets 10 s per session; background statistics 60 s
ALTER ROLE niwis_api_ro SET idle_in_transaction_session_timeout = '60s';

REVOKE ALL ON SCHEMA public FROM niwis_api_ro;
GRANT USAGE ON SCHEMA public TO niwis_api_ro;

GRANT SELECT ON "public"."10_1_borehole_and_aquifer_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."10_1_borehole_and_aquifer_master";
CREATE POLICY niwis_api_ro_read ON "public"."10_1_borehole_and_aquifer_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."10_2_recharge_supporting _master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."10_2_recharge_supporting _master";
CREATE POLICY niwis_api_ro_read ON "public"."10_2_recharge_supporting _master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."11.2_recharge_and_discharge" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."11.2_recharge_and_discharge";
CREATE POLICY niwis_api_ro_read ON "public"."11.2_recharge_and_discharge" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."11.3_aquifer_sustainability" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."11.3_aquifer_sustainability";
CREATE POLICY niwis_api_ro_read ON "public"."11.3_aquifer_sustainability" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."11_1_hydrogeological" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."11_1_hydrogeological";
CREATE POLICY niwis_api_ro_read ON "public"."11_1_hydrogeological" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."12.1_physical_water_quality_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."12.1_physical_water_quality_master";
CREATE POLICY niwis_api_ro_read ON "public"."12.1_physical_water_quality_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."12.2_chemical_water_quality_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."12.2_chemical_water_quality_master";
CREATE POLICY niwis_api_ro_read ON "public"."12.2_chemical_water_quality_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."12.3_biological_water_quality_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."12.3_biological_water_quality_master";
CREATE POLICY niwis_api_ro_read ON "public"."12.3_biological_water_quality_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."12.4_sansa_water_quality_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."12.4_sansa_water_quality_master";
CREATE POLICY niwis_api_ro_read ON "public"."12.4_sansa_water_quality_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."13.1_wastewater_effluent_quality_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."13.1_wastewater_effluent_quality_master";
CREATE POLICY niwis_api_ro_read ON "public"."13.1_wastewater_effluent_quality_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."13.2_WWTW_operational_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."13.2_WWTW_operational_master";
CREATE POLICY niwis_api_ro_read ON "public"."13.2_WWTW_operational_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."13.3_meteorological_variables_(SAWS)_for_WWTW_Compliance_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."13.3_meteorological_variables_(SAWS)_for_WWTW_Compliance_master";
CREATE POLICY niwis_api_ro_read ON "public"."13.3_meteorological_variables_(SAWS)_for_WWTW_Compliance_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."14.1_SANS_241_compliance_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."14.1_SANS_241_compliance_master";
CREATE POLICY niwis_api_ro_read ON "public"."14.1_SANS_241_compliance_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."15.1_ecological_variables_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."15.1_ecological_variables_master";
CREATE POLICY niwis_api_ro_read ON "public"."15.1_ecological_variables_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."15.2_chemical_RWQO_variables_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."15.2_chemical_RWQO_variables_master";
CREATE POLICY niwis_api_ro_read ON "public"."15.2_chemical_RWQO_variables_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."15.3_hydrological_RWQO_variables_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."15.3_hydrological_RWQO_variables_master";
CREATE POLICY niwis_api_ro_read ON "public"."15.3_hydrological_RWQO_variables_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."16_1_complete_wrm_matrix_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."16_1_complete_wrm_matrix_master";
CREATE POLICY niwis_api_ro_read ON "public"."16_1_complete_wrm_matrix_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."17_1_water_quantity_assessment_transfers_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."17_1_water_quantity_assessment_transfers_master";
CREATE POLICY niwis_api_ro_read ON "public"."17_1_water_quantity_assessment_transfers_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."17_2_national_water_balance_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."17_2_national_water_balance_master";
CREATE POLICY niwis_api_ro_read ON "public"."17_2_national_water_balance_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."18_1_water_access_and_coverage_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."18_1_water_access_and_coverage_master";
CREATE POLICY niwis_api_ro_read ON "public"."18_1_water_access_and_coverage_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."18_2_infrastructure_operational_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."18_2_infrastructure_operational_master";
CREATE POLICY niwis_api_ro_read ON "public"."18_2_infrastructure_operational_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."18_3_sansa_eo_support_for_services_masters" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."18_3_sansa_eo_support_for_services_masters";
CREATE POLICY niwis_api_ro_read ON "public"."18_3_sansa_eo_support_for_services_masters" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."19_1_raw_water_tariff_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."19_1_raw_water_tariff_master";
CREATE POLICY niwis_api_ro_read ON "public"."19_1_raw_water_tariff_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."19_2_municipal_water_tariff_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."19_2_municipal_water_tariff_master";
CREATE POLICY niwis_api_ro_read ON "public"."19_2_municipal_water_tariff_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."19_3_water_quality_cost_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."19_3_water_quality_cost_master";
CREATE POLICY niwis_api_ro_read ON "public"."19_3_water_quality_cost_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."4_1_temperature_change_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."4_1_temperature_change_master";
CREATE POLICY niwis_api_ro_read ON "public"."4_1_temperature_change_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."4_2_wet_dry_spells_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."4_2_wet_dry_spells_master";
CREATE POLICY niwis_api_ro_read ON "public"."4_2_wet_dry_spells_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."4_3_mean_annual_precipitation_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."4_3_mean_annual_precipitation_master";
CREATE POLICY niwis_api_ro_read ON "public"."4_3_mean_annual_precipitation_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."4_4_potential_evaporation_daily_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."4_4_potential_evaporation_daily_master";
CREATE POLICY niwis_api_ro_read ON "public"."4_4_potential_evaporation_daily_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."4_5_irrigation_demand_daily_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."4_5_irrigation_demand_daily_master";
CREATE POLICY niwis_api_ro_read ON "public"."4_5_irrigation_demand_daily_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."4_6_streamflow_and_climate_linkage_annual _master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."4_6_streamflow_and_climate_linkage_annual _master";
CREATE POLICY niwis_api_ro_read ON "public"."4_6_streamflow_and_climate_linkage_annual _master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."5.1_primary_satellite_vegetation_variables_sansa_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."5.1_primary_satellite_vegetation_variables_sansa_master";
CREATE POLICY niwis_api_ro_read ON "public"."5.1_primary_satellite_vegetation_variables_sansa_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."5.2_meteorological_drivers_saws_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."5.2_meteorological_drivers_saws_master";
CREATE POLICY niwis_api_ro_read ON "public"."5.2_meteorological_drivers_saws_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."6.1_core_rainfall_variables_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."6.1_core_rainfall_variables_master";
CREATE POLICY niwis_api_ro_read ON "public"."6.1_core_rainfall_variables_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."6.2_rainfall_derived_drought_indices_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."6.2_rainfall_derived_drought_indices_master";
CREATE POLICY niwis_api_ro_read ON "public"."6.2_rainfall_derived_drought_indices_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."7.2_derived_runoff_indicators_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."7.2_derived_runoff_indicators_master";
CREATE POLICY niwis_api_ro_read ON "public"."7.2_derived_runoff_indicators_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."8_1_reservoir_operational_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."8_1_reservoir_operational_master";
CREATE POLICY niwis_api_ro_read ON "public"."8_1_reservoir_operational_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."9_1_hydrological_station_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."9_1_hydrological_station_master";
CREATE POLICY niwis_api_ro_read ON "public"."9_1_hydrological_station_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."9_2_catchment_characteristics_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."9_2_catchment_characteristics_master";
CREATE POLICY niwis_api_ro_read ON "public"."9_2_catchment_characteristics_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."9_3_sansa_earth_observation_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."9_3_sansa_earth_observation_master";
CREATE POLICY niwis_api_ro_read ON "public"."9_3_sansa_earth_observation_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."hydrological_drought_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."hydrological_drought_master";
CREATE POLICY niwis_api_ro_read ON "public"."hydrological_drought_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."meteorological_drought_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."meteorological_drought_master";
CREATE POLICY niwis_api_ro_read ON "public"."meteorological_drought_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."runoff_variables_master" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."runoff_variables_master";
CREATE POLICY niwis_api_ro_read ON "public"."runoff_variables_master" FOR SELECT TO niwis_api_ro USING (true);
GRANT SELECT ON "public"."satellite_remote_sensing" TO niwis_api_ro;
DROP POLICY IF EXISTS niwis_api_ro_read ON "public"."satellite_remote_sensing";
CREATE POLICY niwis_api_ro_read ON "public"."satellite_remote_sensing" FOR SELECT TO niwis_api_ro USING (true);

-- The meta endpoints read table sizes and index lists from the system catalogue (pg_class, pg_indexes), which every
-- role can read: no further grants. Nothing else is granted: no INSERT / UPDATE / DELETE / TRUNCATE, no sequences,
-- no functions, and no default privileges (a new table needs a new registry entry and a regenerated script).

COMMIT;

-- Check (as niwis_api_ro):  SELECT current_setting('default_transaction_read_only');  -- on
--                            CREATE TEMP TABLE t (x int);                              -- must fail (read-only)
