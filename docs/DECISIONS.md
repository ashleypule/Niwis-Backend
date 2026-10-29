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
- **Groundwater depth cannot be forecast nationally**: 38 % of the months since 2000 have no telemetry sample (D35
  allows 20 %); the groundwater forecast tab says so.
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
