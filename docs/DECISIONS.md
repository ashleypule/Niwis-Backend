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
