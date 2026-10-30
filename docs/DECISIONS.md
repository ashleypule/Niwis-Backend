# Decisions

> Decisions taken while completing NIWIS from the database (from 2026-10-20). The owner's standing instructions: the
> database is the authoritative national record and is shown as **Live**; the system is public and open source with no
> login; unclear points are decided here (decision, evidence, alternative considered) instead of waiting for the data
> team. One rule still holds: values that are provably impossible are loading errors and are not published.
> Evidence queries: `scripts/plausibility_checks.sql` (blocks headed §8–§15), `scripts/constancy_check.py`; numbers
> quoted below are from the database on 2026-10-20.

## Data

### D1 What counts as "provably impossible" (applied to every new table)

**Decision.** A column (or individual values) is withheld only when one of these tests fails; everything else is served
as stored:

1. **Constant** — one value in every row.
2. **Identical across districts** — the same value in all districts in more than half of the periods, or more than half
   of the districts sharing one identical time series (`scripts/constancy_check.py`). Static attributes (an area, a soil
   class) are allowed to repeat over time.
3. **Dated after today** — never served (D4).
4. **Impossible in its unit or definition** — a negative concentration, score or flow; a pH that cannot occur in water;
   a magnitude no South African river or aquifer can reach; a quantity that contradicts its own definition (COD below
   BOD₅, transmissivity ≠ conductivity × thickness, a standardised groundwater index that is white noise).
5. **Contradiction within the table** — two columns that describe the same quantity and disagree by a large, systematic
   factor: the family that agrees with the rest of the database is served, the other withheld.
6. **Copies** — a column identical to, or an exact linear rescaling of, a column in another table is not an independent
   value.

Where only a few values are impossible (one negative SASS5 score in 16 744 rows), only those values are dropped
(registry `valid_range`), not the column.

**Evidence.** The same tests produced every withheld column since Phase 0 (DATA_GAPS §3, §4, §15, §18, §20).
**Alternative considered.** Withholding anything that looks synthetic (several §12 columns are spread evenly between
two round numbers). Rejected: "looks synthetic" is not proof of an error, and the owner's rule is to serve everything
that is not provably impossible.

### D2 `8_1_reservoir_operational_master` fails; reservoir storage stays withheld everywhere

**Decision.** `8_1` does **not** become the reservoir source. Storage, % FSC, level, FSL, water area, seepage,
evaporation, district area, runoff, inflow, outflow and NDWI are withheld with their reasons; only catchment rainfall
and soil moisture at the reservoirs are served. The Dam page's storage panels, the drought page's Dam Storage KPI and
the drought cascade's reservoir stage stay null / "not yet available" with the reason.

**Evidence.** 51 of 52 districts hold the same storage (251 997 542.57 m³ = 100 % FSC, level 1 200 m, water area
52.5 km²) on 499 290 district-days from 2000 to today; only Vhembe varies. FSL is 1 200 m in every row; district area is
10 000 km² for every district. Runoff and inflow vary only in 2000 and are one constant per district from 2001 to 2026;
inflow = runoff depth × the 10 000 km² placeholder area (Vhembe 2015-01-28: 6.3465 m³/s both) and outflow ≈ inflow, so
storage never changes. The withheld `hydrological_drought_master` reservoir columns (frozen since Dec 2021) are no
better (DATA_GAPS §15). **Alternative considered.** Serving Vhembe's storage alone (the one district that varies):
rejected — its minimum is the same 251 997 542.57 m³ placeholder on 8 386 days, so its series is not a measurement
either.

### D3 "Siyanda" is Z F Mgcawu; its duplicate rows in `8_1` are not read

**Decision.** "Siyanda" (the name of Z F Mgcawu before 2013) is a district alias: `9_1`, `9_2`, `9_3`, `10_1` and `10_2`
use only that name. `8_1` lists both names (53 "districts"); there the Siyanda rows are excluded (registry
`exclude_locations`). Likewise "Greater Sekhukhune" (the same five tables) is an alias of Sekhukhune. **Evidence.**
In `8_1` Siyanda has the same storage as Z F Mgcawu on all 9 862 days. **Alternative.** No alias (first version of
this decision) — rejected on 2026-10-20: national sums of `9_1` flows were missing Z F Mgcawu.

### D4 Rows dated after today are never served

**Decision.** Every query keeps only rows dated on or before the current date (`Dataset.not_future_sql()`, part of every
column filter, the table statistics, the newest-date query and the category view). The "newest date in the database"
(the staleness reference) is therefore never in the future. **Evidence.** `8_1`, `10_1` and `10_2` hold rows up to
2026-12-31; several monthly tables hold an October 2026 row dated 25 October. **Alternative.** Capping per table by a
registry date — rejected: the rule must hold for tables the data team adds later without a code change.

### D5 A month is complete when the daily table of the same database module covers it

**Decision.** Monthly / annual tables serve only complete periods, judged against the newest daily record of the same
database module (6.2 → 6.1, 5.2 → 5.1, 7.2 → runoff, 9_2 / 9_3 → 9_1, 10_2 → 10_1); modules without a daily table use
the newest daily record in the database. **Evidence.** 6.2 stores a September 2026 SPI computed from 6.1 rainfall that
ends on 6 September; with `9_1` (to 2026-09-24) and `8_1` (to today) registered, the database-wide newest date alone
would have served that partial month. **Alternative.** Keeping one database-wide reference — rejected for that reason.

### D6 `10_1` units are valid UTF-8; a repair mapping guards against mojibake anyway

**Decision.** Units come from the variable names (the long table's equivalent of column names), checked against the
stored `unit` column. `app/core/units.py` has `UNIT_REPAIRS` / `repair_unit()` for text such as "m�/year" or "mÂ³/year".
**Evidence.** The stored bytes are `6d c2 b3` = "m³" (UTF-8) for every row; "m�/day" appears only in viewers that decode
UTF-8 with a Windows code page. **Alternative.** Rewriting the units in the database — not allowed (read-only).

### D7 Two hydraulic-head series, both kept and labelled by their actual unit

**Decision.** `hydraulic_head_amsl_m` (monthly, DWS National Groundwater Archive & DEM interpolation, stored unit
"m amsl") and `hydraulic_head_m_amsl` (telemetry on irregular days, stored unit "m bgl") are both served in **m amsl**.
**Evidence.** The second series holds 448–1 638 m, which as a depth below ground is impossible in South Africa's
aquifers but fits an elevation, as its name says; the two series agree poorly (r = 0.06, 363 m apart on average), so
neither is a duplicate. **Alternative.** Withholding the mislabelled series — rejected: the values are plausible
elevations; only the unit text is wrong.

### D8 `10_1` SGI is withheld; the groundwater SGI stays the `hydrological_drought_master` one (May 2002 – Aug 2024)

**Evidence.** Lag-1 autocorrelation 0.008 (white noise) where this table's water levels persist (depth 0.66, head
0.83) and the earlier SGI has 0.948; r = −0.007 with the table's own depth and 0.001 with its head; every month averages
0 ± 1 across districts, even in the 2015–16 drought. **Alternative.** Serving it because it is current to 2026 —
rejected: it is not an index of any water level in the database.

### D9 `10_1` aquifer constants withheld; `11_1` is the source for aquifer properties

**Evidence.** In `10_1` transmissivity, conductivity, storativity and saturated thickness change at random every year
(year-to-year r ≈ −0.02) and fail T = K × b (r = −0.004); `11_1`'s static properties satisfy it (r = 0.83).

### D10 Surface water (`9_1`): the daily flow family is served, the monthly family withheld

**Decision.** Daily mean flow (m³/day) and daily sediment load are served; `river_discharge_q` is served from Jan 2026
only; monthly mean flow and monthly sediment load are withheld; the surface-water discharge in m³/s is daily mean flow
÷ 86 400 s (a unit conversion of the stored value). **Evidence.** The monthly volume is on average 1/63 of the sum of
the same month's daily flows. Summed over the districts the daily family gives ≈ 40 700 Mm³/year and the monthly family
≈ 650 Mm³/year, against the database's own MAR (16_1) of ≈ 66 300 Mm³/year. From Jan 2026 `river_discharge_q` equals
daily mean flow ÷ 86 400 exactly (City of Cape Town 2026-01-02: 1.9935 m³/s both). **Alternative.** Serving the monthly
family because three of its columns agree with each other — rejected: together they describe 1 % of the country's
runoff.

### D11 Units from the spec for columns named after the parameter only

**Decision.** Columns such as "Turbidity", "pH", "E. coli" carry no unit in their names; their unit is the one in the
spec table of that section (`SPEC_UNITS` in `app/core/units.py`, each entry naming the section), shown as a unit override
in the catalogue. Columns whose section gives no unit (satellite proxies, scores) have none.

### D12 No point map layers: the database has no coordinates

**Decision.** Stations, boreholes, dams and water-quality sites are not point layers; districts remain the map unit and
point layers return 501 with "no coordinates in the database". **Evidence.** No new table has latitude / longitude;
`13.2` and `15.1` have a `.geo` column that is an empty MultiPoint in every row; `8_1` names one combined "reservoir" per
district (two Global Dam Watch ids joined), not individual dams with locations. **Alternative.** Looking up GDW dam
coordinates outside the database — rejected: never publish values that are not in the database.

### D13 `testing_table` stays out of the catalogue

It was a copy of the 4_1 temperature columns; it is no longer in the database (2026-10-20). Kept in `ignored_tables` so
it never reappears as a category if restored.

### D14 `16_2_swsa_strategic_water_matrix_master` is not registered

It is keyed by Strategic Water Source Area (5 SWSAs × 26 years), not by district; the database view and every map
aggregate districts. Listed in `ignored_tables` with that reason. **Alternative.** Registering it with all columns
withheld — rejected: that would show "withheld" for values that are not wrong.

### D15 §13–§16 tables: the database view only

`13.x` (Green Drop), `14.1` (Blue Drop), `15.x` (RWQO) and `16_1` (IWRM) are registered and served on their pages'
"Database variables" sections (`/categories`, `/category/...`); the pages' other panels keep the production "not yet
available" state; `/health` reports these modules as `partial`. Building their full modules is outside this task's five
modules.

### D16 Water-quality tables (`12.x`) are served as stored

Every `12.x` column passes D1. Several are spread evenly between round bounds (e.g. TDS 300–499.8 mg/L with exactly
1 000 distinct values); that is noted in DATA_GAPS §21.4 for the data team, but it is not evidence of an error. EC and
TDS are consistent (TDS = 4.2–6.7 × EC, r = 1.000).

### D17 `13.1` / `14.1` / `15.x` details

`14.1` (drinking water, SANS 241) turbidity, temperature, pH, EC and E. coli are identical to `13.1` wastewater effluent
values in all 16 744 district-months; lead, cyanide (`14.1`) and toxicants (`15.2`) are exact linear rescalings of
`13.1` heavy metals (r = 1.0000); `15.2` pH is `13.1` pH. Those copies, `13.1` pH (below 4 in every row) and DO
(negative in 16 742 rows), COD and BOD₅ (COD < BOD₅ in 16 727 rows), and constants are withheld; `13.1` ammonia and the
`15.1` scores are served without their 2 / 1–3 impossible values.

### D18 `11.2` stores every district-month three times

The three rows are identical; loaders read one value per period (`duplicate_rows: true`). Its baseflow discharge is
withheld (Johannesburg averages 1 739 m³/s).

### D19 Total recharge = direct recharge × district area + indirect recharge

**Decision.** The groundwater-reserve "Total Recharge" (Mm³/year) adds the two recharge components of spec §11.2 as
stored in `11.2`: direct recharge (mm/year, × the district's boundary area) and indirect recharge (m³/year). The
exploitation index shown is the stored `11.3` value (in %, classified as a ratio with the spec's classes), not
recomputed. **Evidence.** The stored index tracks abstraction / indirect recharge (r = 0.84) but is not equal to it,
so recomputing would publish a number the database does not hold. **Alternative.** Direct recharge alone — rejected:
the spec defines both as recharge.

### D20 Water quality: guidelines from the frontend's configuration, CCME WQI

**Decision.** Stored `12.x` values are compared with the guideline table now in `thresholds.json` (`wq_guidelines`:
resource quality objectives for domestic use, DWAF 1996 TWQR / SANS 241, value for value the frontend's configuration;
a test keeps them equal). From that comparison the API derives the compliance rate (share of values within guideline),
"× guideline" ratios (value / upper limit; minimum-only guidelines such as dissolved oxygen as limit / value) and a
**CCME Water Quality Index** (CCME 2001: F1 scope, F2 frequency, F3 amplitude) per district over the trailing 12 months,
classified with the WQI bands in thresholds.json. Combined metal columns are never compared with single-metal limits.
**Evidence.** The spec asks for WQI bands but defines no WQI; the database stores no index. CCME is the published method
the frontend's design already assumed ("CCME-style 0–100"). **Alternative.** No WQI (every WQI panel "not yet
available") — rejected: the index is a documented transformation of stored values against published guidelines, not
an invented number. Note: with the current data most districts pass every guideline except orthophosphate and
chlorophyll-a.

### D21 Background database work never takes every connection

Table statistics run one at a time with a 60 s transaction-local statement timeout (they are cached for an hour and
never requested directly); the category pre-warm shares a limit of `DB_POOL_MAX − 3` connections; the module warm-up
runs one query at a time. **Evidence.** After the §8–§16 tables were registered, cold starts timed out requests and
statistics on the 500 000-row tables (10 s limit) because all five connections were busy warming.

### D22 Drought cascade: streamflow and groundwater stages from the §9 and §10 tables

**Decision.** Stage 4 (streamflow) is the area-weighted **SSI-3 of the `9_1` district flows** (the same per-district
index as the Surface Water page's SSI layer; gamma fit over the full years from 2000), classed with the SPI drought
classes; severity = share of the scope's area at D1 or worse. Stage 6 (groundwater) keeps the SGI with borehole status
classes when a current SGI exists; while it does not (the valid SGI ends Aug 2024 and the `10_1` SGI is withheld), it is
the **`10_1` depth-to-water anomaly** (latest sampled month vs that calendar month's mean, + = shallower; the
Groundwater page's Level Anomaly KPI), shown **without a class or severity** and with the reason, as the soil-moisture
stage. Reservoir stays withheld (`8_1` storage fails the plausibility check, D2). The drought page's SSI-3 KPI stays on
modelled runoff (B8), so the KPI and the streamflow stage are labelled as different series. **Evidence.** The prompt
asks for cascade stages to be updated where a new table supplies valid current data; `9_1` flows and `10_1` depths pass
the constancy and plausibility checks and run to Sep / Oct 2026. **Alternatives.** (a) An SGI computed from `10_1`
depths — rejected: the gamma-fitted standardised index needs gap-free monthly accumulations, and telemetry is sampled
about every seven weeks, so most months would be gaps; a normal-score SGI would be a new method the spec does not
define. (b) Leaving both stages blank — rejected: valid current data exists.

### D23 Public system, no login

**Decision.** NIWIS is public and open source with no login (owner's decision). The frontend has no user menu, role
or sign-out (the header keeps the theme toggle); report covers name only the issuing department; alerts are read-only
(no acknowledge / assign / resolve, nothing stored in the browser). The backend keeps `get_current_user` as a
documented no-op dependency on every router, so access control could be added in one place without changing routes.
**Evidence.** Owner's instruction. **Alternative.** Removing the dependency — rejected: it costs nothing and keeps the
seam.

### D24 Hardening of the public API

**Decision.** Rate limit 600 requests / minute per client IP (health exempt); `X-Forwarded-For` is read only with
`TRUST_PROXY_HEADERS=true` and, when `TRUSTED_PROXY_CIDRS` is set, only from those proxies (uvicorn runs with
`--no-proxy-headers`, so this is the only place the client address is decided). `Cache-Control: public,
max-age=<server cache TTL>` on 200 / 501 GETs (summary and map 15 min, series / tables 1 h, meta / reference /
category lists 1 h), `no-store` on health and errors. CORS from `CORS_ORIGINS`; when empty, `*` — GET / OPTIONS only,
no credentials. Bodies over 16 KiB or chunked → 413, query strings over 4 096 characters → 414. Security headers on every
response (nosniff, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a `default-src 'none'` CSP, with a CSP for
`/docs` that allows Swagger UI's jsDelivr assets; HSTS in production). Unhandled errors return `{"error": "Internal server
error", "requestId"}` only; the trace goes to the log. `/docs` stays public. **Evidence.** Owner's Part 3 list; the
data are public, so caching them publicly is safe and takes load off the 5-connection pool. **Alternative.** CORS
restricted to localhost until a domain exists — rejected: a public, read-only, credential-free API gains nothing from it
and would break third-party use of open data.

### D25 Read-only database role with per-table SELECT policies

**Decision.** `migrations/proposed/001_readonly_api_role.sql` (generated from the registry by
`scripts/gen_readonly_role.py`, kept in sync by a test) creates `niwis_api_ro` with SELECT on the 36 registered tables,
`default_transaction_read_only = on`, `NOBYPASSRLS`, and one `FOR SELECT … USING (true)` policy per table. It is
idempotent and takes the password as a psql variable. `ENV=production` refuses to start unless
`DB_READONLY_ROLE_CONFIRMED=true`. **Evidence.** All 39 public tables have RLS enabled with no policies (catalogue query,
2026-10-21), so a plain role would see no rows; the script was run twice on the local fixture database: the role reads
under RLS and `CREATE TEMP TABLE` fails (read-only). **Alternative.** `BYPASSRLS` — simpler, but it would also bypass
any restrictive policy added later. The backend has not run it on Supabase (read-only rule).

### D26 Open-source release: items DWS should confirm before publishing

**Decision.** Both repositories are prepared for public release (CONTRIBUTING.md, SECURITY.md, gitleaks configuration,
no secrets in history), with **no LICENSE file**: each README says "Licence: to be confirmed by DWS". The following may
not be publishable as they stand and are listed for DWS to decide; nothing was removed or rewritten:

| Item | Where | Why it needs a decision |
| --- | --- | --- |
| DWS logo / coat of arms | frontend `public/brand/dws-logo.png`; drawn in the sidebar, header and report covers (screenshots are not committed) | Government insignia: use is governed by DWS / GCIS corporate-identity rules, not by the code licence. |
| Mockup screenshots | frontend `docs/reference/screens/` (61 images) | Design mockups supplied for the build; their author and publication status are not stated. |
| Confidential data requirements specification | backend `reference/NIWIS-data-requirements-spec.md` — **git-ignored, never committed** (full-history check 2026-10-21) | Marked confidential. Code and docs cite its section numbers (§3–§16) and implement its thresholds (`app/core/thresholds.json`); DWS should confirm that citing and implementing them publicly is acceptable. |
| Database extracts | backend `tests/fixtures/*.sql.gz`; frontend `tests/fixtures/backend/*.json` | Real values from the NIWIS database (owner: authoritative, shown as Live). Publishing the data is DWS's call, separate from publishing the code. |
| WMA and catchment boundaries | `reference/geo/wmas.json`, `catchments.json` (both repos) | From the DWS NWRP map service; `SOURCES.md` already says to confirm the licence with DWS GIS. geoBoundaries layers (CC BY) need the attribution the map footer shows. |

**Evidence.** Repository inventory and gitleaks scan, 2026-10-21. **Alternative.** Deleting or rewriting these now —
rejected: the owner asked for them to be listed, and history is not rewritten without the owner.

### D27 Database hosting region

The Supabase project runs in AWS `eu-west-1` (Ireland). This is recorded as a **hosting choice**, not a POPIA blocker:
the database holds aggregated environmental and water-resource data per district (no personal information), and
POPIA's cross-border rules (section 72) concern personal information. If personal data are ever added (e.g. settlement
or customer records), the hosting region must be reassessed. **Evidence.** Pooler host `aws-1-eu-west-1.pooler.supabase.com`;
`docs/DB_INVENTORY.md` (no personal-data columns in the 36 registered tables). **Alternative.** Moving to a South African
region (e.g. `af-south-1`) — a data-team decision, not required for this release.

### D28 Production builds talk only to the API; every contract path answers

**Decision.** The production frontend is built in `api` mode (`npm run build:release`, `.env.release`): no Mock
Service Worker, so no sample number can reach a production page. For that to work the API answers every path the
frontend calls: `/api/v1/<module>/<summary|timeseries|forecast|breakdown|table|matrix|map|entities…>` of the 14
modules without a live service returns **501 `DATA_UNAVAILABLE` with a module-specific reason**
(`app/modules/unavailable.py`; e.g. "No tariff or water-pricing tables are in the database"; Green Drop, Blue Drop,
RWQO and IWRM say that only their Database variables are served), and so does `/api/v1/alerts`. The frontend shows
these as "Not yet available" with the reason. `/api/v1/reference/local-municipalities` (213, with their district) and
`/api/v1/search` (provinces, WMAs, districts, local municipalities) are now served from the committed reference files,
so filters and search work without the mock. Paths of live modules, unknown modules and unknown kinds stay 404.
**Evidence.** Before this, a production build showed red error panels (404) on 14 pages, and its filter bar asked the
mock for local municipalities. **Alternative.** Keeping hybrid mode in production with sample panels marked Sample —
rejected: the owner's rule is that production builds never show sample numbers.

## Forecasting (from 2026-10-23)

> Owner's instruction: NIWIS makes its own statistical predictions from the database — any live numeric column, any
> area, a chosen model or Auto, validated on held-back recent data, refitted, forecast with an uncertainty range and
> compared with simple baselines — and the same machinery fills the pages' forecast tabs. The database stays read-only;
> forecasts are never written to it. Code: `app/forecasting/`; results on real data: `docs/FORECAST_VALIDATION.md`.

### D29 What can be forecast

**Decision.** A Studio target is any catalogue column that is **live**, **numeric** and **daily or monthly**
(`GET /api/v1/forecast/targets`). Withheld and duplicate columns are refused (400 with the reason); annual columns are
not offered (≤ 27 values, below the 36-month minimum). Pages may also forecast four **derived** variables defined in
`app/forecasting/pages.py` by an SQL expression over a registered column, the way the pages derive them: runoff depth
(the registry's volume → mm transform), hot days (district days with Tmax > 35 °C, summed per month), % of the area in
D1–D4 (district SPI-6 ≤ −0.8 after rounding, area-weighted) and river discharge in m³/s (9_1 m³/day ÷ 86 400).
**Alternative.** Forecasting annual columns with a shorter minimum — rejected: a 26-point backtest cannot tell models
apart.

### D30 Monthly aggregation follows the variable type (`aggregate: sum | mean` in the catalogue)

**Decision.** `app/registry/catalogue.py` → `aggregate_of`: **sum** for amounts — units mm, mm/day, days, m³, m³/day,
t/day, kWh/day (rainfall, evaporation, runoff, irrigation, day counts, daily volumes) unless the name says the value is
a state of the period (anomaly, mean, max, min, peak, ratio, intensity, index, %, flag); **mean** for everything else
(temperatures, SPI / SPEI, NDVI, % FSC, levels, flows in m³/s). Written to `catalogue.yaml` for every numeric column
(56 sums). A month's **sum** needs every day of the month but one (the database's own monthly totals miss 31 December);
a month's **mean** needs one value (telemetry is sampled about every seven weeks). **Evidence.** National and Western
Cape monthly sums of `6.1` daily rainfall equal the stored `Monthly_Total_Precipitation_mm` in every month 2000–2026
(Supabase, 2026-10-23); `tests/test_forecast_api.py` checks the sum against SQL on the fixture. **Alternative.**
Requiring every day — rejected: every December would be a gap.

### D31 The series is the dashboard's series

**Decision.** District values are combined exactly as the dashboard does: an area-weighted mean with ≥ 95 % of the
area reporting, or, for volumes and flows, a sum that needs every district (WMA: the district's share of area). Only
rows the dashboard serves are read (never after today, inside `valid_from` / `valid_until`, impossible values dropped,
excluded spellings skipped), and incomplete months are never used (D5). Whole-record reads of daily tables run with a
60 s transaction-local timeout and hold the background connection limit (D21). **Alternative.** Forecasting each
district and adding the forecasts — rejected: 52 runs per national forecast, and the scope rules would differ from the
charts.

### D32 Models offered

**Decision.** Baselines (naive, seasonal naive, same-month climatology, 12-month moving average) always run.
Statistical: ETS (additive error, yearly seasonality, with / without a damped trend by AIC), SARIMA (seasonal
differencing when the STL seasonal strength is ≥ 0.64, KPSS for d, then 4 × 1–2 (p, q)(P, Q) candidates by AIC,
refitted on every training window), SARIMAX (inputs as regressors), Prophet (monthly, yearly seasonality). ML: Random
Forest and **LightGBM** (chosen over XGBoost: one small wheel, no GPU build questions; the spec's "RF / XGBoost" family)
on lags 1, 2, 3, 6, 12, 3- and 12-month rolling means and the calendar month, applied recursively. Deep (experimental):
LSTM (12-month lookback, 64 → 32, dropout 0.2) and a 2-layer Transformer encoder, PyTorch CPU, early stopping on the
last 15 % of training windows in time order, fixed seeds. Multivariate: VAR on series standardised per calendar month,
lag by AIC ≤ 6. **Auto** runs every non-experimental model the series allows and keeps the lowest backtest MASE (ties:
the simpler model); **Compare all** adds the experimental ones. Deep models run only without input variables
("single-target runs"). Daily targets (1–30 days): baselines, ETS and ARIMA on the anomaly from the training window's
day-of-year mean, over its last three years. GARCH is not offered (volatility, not levels). **Alternatives.** Deep
models in Auto — rejected: with ~300 monthly points they rarely win and cost most of a run; Compare all includes them.
A full stepwise auto-ARIMA — rejected: about 30 s per model per run; the reduced grid is refitted on every window.

### D33 Validation

**Decision.** Rolling-origin backtest with 4 origins (3 when the history is short), `min(h, 12)` months apart, the last
one ending on the last observation; each origin's model sees only data before it; gaps are filled inside each training
window from that window only (same-month mean + interpolated anomaly); inputs are forecast at each origin too. Metrics:
MAE, RMSE, MASE (each origin's MAE ÷ the in-sample MAE of the seasonal naive forecast on its window, averaged), skill
against climatology = 1 − SSE / SSE of the climatology baseline on the same points (> 0 beats the long-term average),
sMAPE when the series reaches zero or changes sign (SPI, anomalies, dry months), MAPE otherwise, and the coverage of
the 10–90 % band. For a model without its own interval, an origin's coverage uses the band built from the **other**
origins' errors. **Evidence.** `tests/test_forecast_engine.py::test_backtest_never_sees_data_after_its_origin` changes
every value after the first origin (target and input) and the first origin's predictions stay identical.
**Alternative.** A single hold-out year — rejected: one origin cannot rank models reliably.

### D34 Uncertainty and bounds

**Decision.** 10th–90th percentile band (as elsewhere on the dashboard): the model's own where it has one (ETS, SARIMA,
SARIMAX, Prophet, VAR); otherwise the 10th / 90th percentiles of the backtest errors at that lead time, pooled over lead
times h ± 2 and made non-narrowing with the horizon. Forecasts and bands are clipped to physical limits: ≥ 0 for
quantities whose history never goes below 0 (not for anomalies, indices, °C or elevations), ≤ 100 for % shares.
SPI / SPEI runs report the D0–D4 class of the median and the probability of each class from a normal distribution
centred on the median with the band as ±1.2816 σ, using the spec §3.4 thresholds as published (rounded to 2 dp, ≤ the
class maximum). **Alternative.** Conformal intervals — deferred: four origins give too few errors per lead time.

### D35 Limits and data checks, answered at once as 422

**Decision.** Monthly horizon 1–24 months, daily 1–30 days; at least 36 months (deep models 60; daily 730 days); a
series that is constant, or has more than 20 % of its months missing, is refused (a warning above 10 %, spec §22.2);
at most 3 inputs; inputs must reach the target's last month, else the forecast would start in the past. These checks
run synchronously on `POST` (no model is fitted), so the client gets **422 `UNPROCESSABLE`** with the reason instead of
a queued run that fails. Future input values are the inputs' own forecasts from the better of ETS and SARIMA on the
window's last 12 months, never the last value held constant; the result says so.

### D36 Background runs on a public API

**Decision.** Each run trains in its own spawned process, at most `FORECAST_WORKERS` (2) at once; a run past
`FORECAST_TIMEOUT_S` (120 s) is killed (a process per run is what makes a hard timeout possible). Workers pin BLAS /
OpenMP / torch to one thread. Results are cached by a hash of the normalised request **and the data it was trained on**
(target and input values, hence their `asOf`): the same request on the same data never trains twice; a timeout or a
worker crash is not cached. `FORECAST_RUNS_PER_HOUR` (10) new runs per client IP (cache hits free; the IP is the one the
rate limiter decided, D24), `FORECAST_QUEUE_LIMIT` (20) queued or running → 503 with `Retry-After`, runs discarded after
`FORECAST_RETENTION_H` (24 h). Page forecasts use at most `FORECAST_WORKERS − 1` slots, so a Studio run never waits
behind a round of page forecasts. CORS now allows POST (only the forecast runs take a body); run status is `no-store`.
**Alternative.** A persistent process pool — rejected: a stuck fit could not be stopped without killing the pool.

### D37 Page forecasts

**Decision.** At startup and whenever the newest date in the database changes (checked every 30 min), Auto 24-month
forecasts are computed for South Africa and the nine provinces (national first) of: SPI-3, SPI-6, SPEI-3, area in
D1–D4 (drought); monthly rainfall, SPI-1 (rainfall); temperature anomaly, hot days (climate); NDVI, VCI (vegetation);
runoff depth, runoff anomaly (runoff); river discharge (surface water); depth to groundwater (groundwater). They feed
the contract endpoints that answered 501: drought `forecast?metric=spi` (`scale` 3 / 6, `horizon`), `spei`, `area`,
`table forecast-indicators` / `forecast-kpis`, `breakdown class-probability` (1–6 months), `matrix province-risk`
(P(D2+) at 3 / 6 / 12 / 24 months); rainfall `rainfall`, `spi` (`scale` 1 / 3); climate `temp-anomaly`, `hot-days`
(new keys); vegetation `ndvi`, `vci`, `table forecast-indicators`; runoff `runoff`, `runoff-anomaly`; surface water
`flow`, `runoff`; groundwater `level`; AI `drought`, `streamflow`, `groundwater`, `table models`; Overview `ai`. Every
response carries `forecast` (model, why, skill, MASE, coverage, data date, disclaimer, Studio link); a model without
skill says "This model does not beat the long-term average for this variable". Other scopes answer 501 pointing to the
Studio. **Still 501, with the reason:** district outlook maps (no district forecasts are precomputed), settlements and
population (not in the database), recovery probabilities, flood probability, reservoir storage (withheld, D2), demand,
water-quality index (computed, not stored), climate scenarios; the runoff page's `flow` (modelled per district) points
to the Surface Water forecast. Groundwater depth is served only if its gaps allow (D35; see FORECAST_VALIDATION).
**Alternative.** Computing districts too (52 × 14 runs per data change) — rejected for the worker budget; any district
can be run in the Studio.

### D38 Dependencies

**Decision.** statsmodels, scikit-learn, LightGBM, Prophet and PyTorch (CPU wheel from the PyTorch index) are runtime
dependencies; the API process never imports torch (only worker processes running a deep model do). The Docker image
grows from 634 MB to 2.19 GB (measured 2026-10-29; torch and its CPU libraries are most of it). **Alternative.** A separate forecasting service — rejected for now: one deployable is simpler,
and the worker processes already isolate the training.

### D39 Two new database tables are listed, not registered

`17_1_water_quantity_assessment_transfers_master` and `17_2_national_water_balance_master` appeared in the database by
2026-10-23. The catalogue lists them as unregistered (its rule for any new table) until they are profiled and
plausibility-checked (D1); registering them is outside the forecasting work. `tests/test_new_tables.py` names them.

### D40 What the validation on the real data shows, and what the pages say about it

**Findings** (`docs/FORECAST_VALIDATION.md`, generated 2026-10-29 from the national series, every model, four
rolling origins, nothing tuned):

- **At 3 months ahead the forecasts have real skill.** Against the same-month climatology: SPI-6 0.84 (LightGBM),
  VCI 0.85 (last value), NDVI 0.68 (ETS), SPI-3 0.51 and SPEI-3 0.43 (LightGBM), monthly rainfall 0.43 (LightGBM),
  river discharge 0.35 and area in drought 0.28 (seasonal naive), runoff anomaly 0.20; temperature anomaly and hot
  days 0.00 (nothing beats their climatology).
- **At 24 months ahead almost nothing beats the long-term average.** Auto picks the climatology itself for SPI-3,
  SPI-6, area in drought, rainfall, runoff and discharge (skill 0.00 by definition); SARIMA edges it for SPEI-3 (0.02),
  temperature anomaly (0.00) and hot days (0.02); only vegetation keeps skill from persistence (NDVI 0.32 seasonal
  naive, VCI 0.28 ETS). The experimental deep models never win Auto's comparison and would not have (LSTM best 0.14,
  Transformer 0.32 on NDVI, below the baselines that won).
- **Groundwater depth cannot be forecast nationally** (as of D40): 38 % of the months since 2000 have no telemetry
  sample (D35 allows 20 %). Superseded by D41.3: the gaps are a six-weekly sampling cadence and are interpolated.
- Band coverage of the ML models' residual intervals is below the 80 % expected at 3 months (58–75 %), i.e. their bands
  are somewhat narrow; the statistical models' own intervals cover 74–95 %. The result's warnings say so when it happens.
- A Compare-all run of one variable takes 25–155 s on one worker thread (SARIMA's order search and the two deep models
  are most of it); the page forecasts (Auto, no deep models) take 13–45 s each.

**Decision.** The page forecasts stay **Auto at 24 months** (the indicator tables need 3 / 6 / 12 / 24 months from one
run), and the pages say what that means: the `forecast` block and the note under every forecast chart carry the
model, its skill and "This model does not beat the long-term average for this variable" whenever the skill is ≤ 0 — on
2026-10-29 that is the case for SPI-3, SPI-6, area in drought, rainfall, runoff and discharge at 24 months. Users who
want the 3-month skill run the variable in the Studio with a 3-month horizon (one click from the tab's Studio link).
**Alternative considered.** A second round at 3 months for every variable and scope — rejected for now: it doubles the
background work (≈ 140 runs per data change) for a product the Studio already gives; revisit if the forecast tabs
become a daily tool.

### D41 Every dashboard's forecast tab served: any area, derived series, forecast products (2026-10-29)

> Owner's instruction: the Predictions & Forecasting tab of every dashboard must be fully functional, with the
> Forecasting Studio as the shared advanced tool underneath, not instead of it.

**D41.1 Districts with the quick models, everything else on demand.** The round of D37 (Auto, 24 months, South
Africa + 9 provinces) is followed by a district round of the six outlook variables (SPI-3, SPI-6, rainfall, NDVI, VCI,
WQI) with a new choice, `auto-fast`: Auto over the baselines, ETS and LightGBM only. Per `docs/FORECAST_VALIDATION.md`
these fit in 0–5 s where SARIMA's order search, Prophet, Random Forest and the deep models take 5–45 s, and on the
national series the quick set contains the winner, or one within 0.05 MASE of it, for every variable but SPEI-3,
temperature anomaly and hot days (SARIMA). 52 × 6 = 312 quick runs ≈ 15–25 min on one background slot after the
national round. Any other (variable, area) pair — a district's SPEI-3, a WMA's runoff — is computed with `auto-fast`
the first time its tab is opened: the first answer is 501 `no-store` "being computed (about a minute); reload
shortly", the next is 200; the entry lives until the data change. On-demand runs use the Studio's slots (never behind
the background round), are not rate-limited (no client address), and a full queue defers them to the next request.
**Alternatives.** Every variable for every district in the round (52 × 20 Auto runs ≈ 8 h per data change) —
rejected; districts only in the Studio (D37) — rejected by the owner's instruction: a user who selects a district
expects the tab to work.

**D41.2 Derived page variables computed in Python.** A `Target` may carry a `loader` (the scope's monthly series
from the module's own method) instead of a SQL column: the CCME WQI (the Water Quality page's `wqi_frame`, area-
weighted) and the Runoff page's modelled flow (monthly mean depth × district area, summed). They are page variables
only — the Studio forecasts stored columns — so their `forecast.studio` link is null. Nitrate, orthophosphate,
turbidity and E. coli are ordinary 12.x monthly columns (complete from 2000 nationally and per district).

**D41.3 Groundwater depth: short gaps interpolated.** The 10_1 telemetry holds 7–8 of 12 months in every year since
2000, for every province alike (checked 2026-10-29): a sampling cadence of about six weeks, not outages. A level
changes slowly, so single gaps of at most 2 months are interpolated linearly before the 20 % rule (D35); longer gaps
stay missing and the series note says how many months were interpolated. The gap rule itself is unchanged for the
Studio. Validated 2026-10-29 (`docs/FORECAST_VALIDATION.md`): the national series (321 months, 121 interpolated)
forecasts with a 12-month moving average, MASE 0.13, skill 0.96 against climatology (the depth trends steadily, which
the same-month mean cannot follow); this supersedes D40's "cannot be forecast nationally". **Alternative.** Raising
the 20 % limit — rejected: it would admit series with real outages.

**D41.4 Probabilities from the band.** Every probability product uses the engine's own convention (D34 class
probabilities): a normal distribution centred on the median with the 10–90 % band as ±1.28 σ. Flood probability =
P(monthly flow > Q10), Q10 = the 90th percentile of the variable's monthly history (the flow exceeded 10 % of the
time); early-warning confidence = the probability of the forecast class 3 months ahead (for the area in drought, of
the forecast direction); recovery = 100 − P(D1–D4); WQI band outlook = the probability of each band 1–12 months ahead;
vegetation recovery = P(VCI ≥ 40 %, Kogan's stress threshold) in 12 months. The notes say so.

**D41.5 Outlook maps.** The district median at the lead time: SPI-3 at 1 / 2 / 3 months (`outlook-1/2/3`), SPI-6 at
6 months (`forecast-6`, the drought-class map's variable), NDVI at 12 months and its departure from the district's
same-month mean of its own record (`forecast-anomaly`; the page's observed anomaly map uses 2001–2025 for the same
reason), WQI at 12 months, rainfall at 1–3 months as the anomaly in % of the district's same-month mean (the
contract's `anomalyPct` scale, 0 = normal, rather than "% of normal" with 100 = normal). A district without a forecast
is null; the title carries the target month.

**D41.6 Climate `forecast-summary` / `risk-outlook`: trend extrapolation, labelled.** The database holds no climate
scenarios (D37 kept these 501). The tables now extrapolate the observed Sen's-slope trend of the annual record
(2000–last full year) to 2030 / 2050 / 2070 from the record's mean as baseline, and grade the 2050 change in the
adverse direction against the record's interannual standard deviation (< 0.5 Low, < 1 Moderate, < 2 High, else Very
High; an insignificant trend, p ≥ 0.05, is Low). `baseline` / `baselineNote` say "not a climate-model projection";
there is no `forecast` block (no engine run). Indicators with fewer than 8 full years are null with the reason.
**Alternative.** Leaving the two tables 501 — rejected: the tab would stay empty for a product a trend can honestly
inform, as long as it is named for what it is.

**D41.7 What stays 501.** Settlement / population impacts (no register), reservoir storage and every dams forecast
(withheld, D2), water demand, climate scenarios, feature importance: no data to forecast from.

**D41.8 Response checks.** A forecast response may carry values to the end of its horizon
(`forecast.forecastStart` + `horizon`); the coverage invariant allows that instead of being bypassed by naming.

### D42 §17 water-quantity tables registered: 17_1 served to 2024, most of 17_2 withheld (2026-10-29)

**Evidence (profiled 2026-10-29).** `17_1_water_quantity_assessment_transfers_master`: 16 districts of the Vaal /
Orange / Thukela transfer systems, one row per district-month 2000-01 – 2026-10; transfer rates (m³/day) vary by
district and month, but every transfer, loss, energy and deficit column is **zero in all 16 districts from January
2025**; donor-basin storage is 10.0–15.3 % FSC in every row. `17_2_national_water_balance_master`: one national row
per month (no district column). Against the national totals its columns are implausible in their stated unit
(Mm³/month): precipitation ≈ 3 200 (≈ 38 000/yr where MAP ≈ 600 000 Mm³/yr), runoff ≈ 265 (≈ 3 200/yr where MAR ≈
49 000), recharge up to 99 000 in a month (national ≈ 30 000/yr), municipal demand ≈ 3 900 and agricultural ≈ 10 000 per
month (≈ the country's *annual* use), reserve ≈ 32/month; net inter-basin transfers (83.33) and industrial demand (153)
are constants; the balance is computed from those columns. Evapotranspiration (≈ 370 000 Mm³/yr) and surface storage
(25 600–32 200 Mm³, the national dam capacity) are consistent with the national figures.

**Decision.** Both tables are registered (database module 17, contract module `water-quantity`, also serving
`iwrm`). 17_1's transfer columns are served with `valid_until: 2024-12-31` (the zero fill is not measured); donor
storage is served with its note. 17_2's evapotranspiration and surface storage are served; the other ten columns are
withheld with the comparison in their reason (D1 rule 4). A **national table** is a new registry case
(`location: {column: null}`), read as the location `ZA`; the API serves it for South Africa only. **Alternative.**
Rescaling 17_2 to plausible units — rejected (D5: placeholders are withheld, never repaired).

### D43 Compliance modules derive district scores from published limits (2026-10-29)

**Decision.** `green-drop`, `blue-drop` and `rwqo` serve a **district compliance score** = share of the module's
served parameters within their limit in a month: 13.1 effluent values against the General Authorisation general
limits (the frontend's `EFFLUENT_LIMITS`, now in thresholds.json), 14.1 drinking-water determinands against SANS
241:2015, 15.2 / 15.3 values against the objectives the tables themselves state in their `*_Limit` columns (one
text per column in every row). Only served columns are judged: 13.1 pH / DO / BOD₅ / COD and the 14.1 and 15.2
copies of effluent values stay withheld (D17), as do the stored compliance flags and scores built on them. Scores are
banded with the frontend's Green Drop / Blue Drop bands and the Present Ecological State is classed A–F from the
habitat-integrity score with the Kleynhans (1996) bands; every note says the scores are not the official DWS
assessments. Districts stand in for plants, systems and sites (none exist in the database). **Alternative.** Serving
the stored `*_Compliant` flags — rejected: they judge copied and impossible values (D17).

### D44 Alerts are derived on request, not stored (2026-10-29)

**Decision.** No alert register exists, so `GET /api/v1/alerts` computes alerts from the latest complete period of
the live data and the thresholds in thresholds.json: SPI-6 drought classes, VCI stress, the CCME WQI bands, the
exploitation index, and the effluent / SANS 241 compliance scores (D43). Severities follow the class order (D3 / D4
critical; D2, VCI < 20 %, WQI < 25, over-exploited, score below the critical band high; the next band moderate).
Every alert is `open` and `unread` (nothing is acknowledged or resolved anywhere), dated by its period, identified by
module, rule, district and month, and cached until the newest data date changes. **Alternative.** Keeping `/alerts`
501 until an alert service exists — rejected by the owner: a derived "what is beyond a threshold now" is what the
page can honestly show; an operational log is not.

### D45 Data-integration and governance pages from the API's own metadata (2026-10-29)

**Decision.** The two pages serve what the API already knows about its database (`/meta/sources`, the registry and
the catalogue): tables, rows, newest dates, staleness, completeness, served / withheld / duplicate columns with their
reasons, resolutions and spec sections, plus a four-dimension quality score (completeness, validity, timeliness,
coverage). Providers, feeds as organisations, ingestion volumes over time, access requests, usage, standards
assessments and retention classes are not recorded and stay 501 or null with the reason. The pages are the same for
every scope. **Alternative.** Keeping the pages 404 — rejected: most of their panels are facts the API holds.

### D46 §18 / §19 tables registered: local-municipality rows aggregated to districts (2026-10-29)

**Evidence (profiled 2026-10-29).** `18_1` (access and coverage): 145 `Region` values (local municipalities,
metros, districts and one HTML fragment), financial years "April 1994" – "April 2026", a `Category` of Total /
Urban / Rural (the split exists for April 2026 only); the three service-level household counts are one value per
municipality in every year (a census snapshot) and their percentages reach 1 506 %; "distance to the nearest water
point" holds three values. `18_2`, `19_1` – `19_3`: 124 municipalities and metros (plus "National") × 23 years
(2004–2026, `Standardized_Year`); percentages stored as text with thousands separators next to computed numeric
twins; ratios of a zero base stored as ±infinity; negative volumes and losses; one constant column each in 18_2
(outage duration) and 19_2 (free allocation); 19_2's "electricity cost" equal to 18_2's pump energy. `18_3`: per
district-year, a "PDSI" of −708 … +913. `19_1`'s raw tariff and capital charge are one national value per year.

**Decision.** All six are registered. A table keyed by local municipality is a new registry case
(`location.level: local_municipality`): each `Region` is mapped to its district with the 213-municipality file
(from the frontend's centroid mapping, plus the tables' own spellings) and the rows are combined per district by
the column's `agg` (sum for households, volumes and annual costs; mean for rates, tariffs and prices). 18_1 reads
the Total rows (`row_filter`); the Urban / Rural rows feed one breakdown. Withheld (D1): the three service-level
percentages, the distance column, the outage duration, the free allocation, the PDSI. Served inside valid ranges:
the negative and infinite 18_2 / 19_2 / 19_3 values. The static service-level counts are served as a snapshot with
a note. Scores and shares are recomputed from the sums, never averaged from percentages. The pages' entities are the
municipalities themselves (their own latest rows), which is what the contract asks for (213 × municipality).
**Alternatives.** Registering 18_x / 19_x as district tables by renaming — impossible (several municipalities per
district); serving municipality values as districts — rejected (sums would be wrong). The annual series are too
short for the forecasting engine, so the pages' forecast keys stay 501.

### D47 The derived dashboards count as live; their remaining gaps are filled from existing data (2026-10-30)

**Question.** `/health` listed only the table-backed modules, so the header read "17 of 24 modules live" while the
AI, alerts, data-integration and governance pages — served from other modules' tables and the API's own metadata —
were live too. Those pages also still answered 501 for panels that could be filled from what the database holds.

**Decision.** `/health` lists every dashboard of the contract: the derived modules as live with the tables they read
and a reason saying what they are derived from, the Overview as partial (its panels belong to other modules), and
settlements and roadmap as unavailable with their reasons. 21 of 24 are live. The gaps closed without new data:

- `ai`: summary KPIs from the scope's page forecasts (models, mean skill vs climatology — labelled as such, not
  NSE), the SPI-3 forecast (6-month drought probability), the derived alerts (early warnings) and the run log (runs
  in 30 days, counted since the API started); feature importance from a LightGBM fit on the national SPI-3 series
  (gain share of the eight lag features). Reservoir outlook stays null (D2); demand and scenario forecasts stay 501.
- `alerts`: four more rules with the pages' own thresholds (services NRW, RWQO compliance, IWRM losses, tariff
  increases).
- `data-integration` / `governance`: records per month by source group counted from the rows' own dates (the
  database has no load log, and the chart says so); a district coverage map from the tables' district spellings;
  providers as named in the tables' names (SANSA, SAWS — the only provider record the database holds; the other 38
  tables are listed as "not named"); the specification's retention policy as a table that says it is a policy, not
  an observation.

**Not filled, and why.** Access requests, usage, standards compliance and provider locations: nothing in the
database records them. Model skill as NSE: the engine scores against climatology (skill, MASE, coverage), and
re-labelling skill as NSE would be false. Run history across restarts: runs are not stored (the database is
read-only).

## System audit (from 2026-10-30)

### D48 Step 1 of the hand-over audit: what the independent recomputation changed (2026-10-30)

**Question.** Before the hand-over to DWS every number on every live page was recomputed with independent SQL on the
database (`docs/AUDIT_STEP1_DATA.md`, one row per panel, three districts spot-checked per page). Most panels matched;
eight did not. The decisions taken while fixing them:

1. **Classes are decided on the published 2-dp value.** Spec §3.4 rounds an SPI to 2 decimals and then compares. The
   database stores 3 decimals as double precision, and `drought_class` rounded the binary float (as JavaScript's
   `Math.round` would): a stored −1.295 became −1.29 (D1) where the published −1.30 is D2. Classes now round the
   value's decimal representation half away from zero, and `rnd` (every displayed number) rounds the same way, so a
   displayed −1.30 never sits next to a D1 pill. 127 stored SPI and 38 SPEI values sit on such ties; the newest one
   served is City of Cape Town's SPI-6 of March 2026 (−1.595 → D3). **Alternative.** Keeping float rounding to match
   the frontend — rejected: the frontend's `droughtClassFromSpi` has the same artefact and is to be changed the same
   way (listed for the frontend).
2. **A scope's ratio is the ratio of its totals.** The Runoff Coefficient and Baseflow Index KPIs averaged the 52
   district ratios by area (0.186) while their labels and the statistics table meant Σ runoff / Σ rainfall (0.198).
   Scope KPIs that are ratios of amounts are now computed from the area-weighted totals; for one district nothing
   changes. **Alternative.** Relabelling the KPI "mean of district coefficients" — rejected: the national coefficient
   people expect is the ratio of totals, which is also what the statistics table and the map's district values use.
3. **Monthly means are means of whole months.** The Runoff and Surface Water discharge charts drew a "monthly mean"
   for the window's first and last months from the few days inside the window (7–30 Sep 2025: 2 823.6 m³/s against a
   whole-month 2 342.5; 1–6 Sep 2026: 9 983) next to full-month normals. The daily line keeps the window; the monthly
   step and its normal now cover complete months only.
4. **A forecast tab's history is the dashboard's series.** The runoff-anomaly page forecast was built on the
   area-weighted mean of the districts' stored `Runoff_Anomaly_percent` (Aug 2026: 42.8 %), a mean of percentages,
   while the page chart shows the anomaly of the area-weighted depth against the area-weighted 7.2 normal (11.6 %).
   The page variable is now a loader over `RunoffService.anomaly_series()`, the one implementation behind the chart;
   the next page-forecast round recomputes it. **Alternative.** Changing the chart to the mean of percentages —
   rejected (DATA_MAPPING §0.2: anomalies are recomputed from aggregated quantities, never averaged percentages).
5. **One rule for extensive units.** The database category view summed Mm³, Mm³/month, m³/s, m³/day, m³/year and
   km² over a scope's districts and averaged everything else; the pages had a slightly different set. Mm³/year
   (16_1 mean annual runoff, inter-basin transfers, water balance, return flow; 11_1 dynamic reserve), t/day (9_1
   sediment load) and kWh/day (13.2 power consumption) are extensive too, and their national values were area-weighted
   means (16_1 MAR 2 480 Mm³/year for a national sum of 86 908). `app/core/units.py` `EXTENSIVE_UNITS` is now the
   single rule used by the pages (`DistrictModule.extensive`), the category charts and the page forecasts; monetary
   and per-unit quantities (R/year, L/year per household, m³/h per borehole, kWh/Ml) stay area-weighted because a unit
   alone cannot tell a total from a rate. The Surface Water page's sediment-load chart and statistics row are sums
   now, labelled.
6. **Labels say what a period really covers.** The ET₀ "annual" KPI sums 364 days (the source table has no
   31 December) and now says so. The 365-day "12 months" windows ending on the newest daily record (rainfall, runoff)
   were kept: every such panel states its dates, and ending them on the last complete month instead is a product
   decision for the owner (listed under observations).

**Not changed, listed for step 3** (`docs/AUDIT_STEP1_DATA.md`, "Derivable in step 3"): land-cover shares from 9_2,
a derived peak flow and district flow-status shares on the Runoff page, the WQI on the Surface Water page, and an SGI
from the 10_1 telemetry (needs a method decision).

### Pending review (2026-10-30)

None: every table in the database is registered or ignored with a reason (the six §18 / §19 tables were registered
the same day they appeared, D46).
