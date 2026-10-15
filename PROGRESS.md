# Progress

## Status: all phases complete (2026-10-14). Five modules live; hardening done. Waiting on the data team (below).

### Blocking / waiting on others

- **Read-only role** (data team): API connects as `postgres` via the session pooler; every session is forced READ ONLY
  (verified: writes rejected). Proposed role: `migrations/proposed/001_readonly_api_role.sql`.
- **Indexes** (data team): `migrations/proposed/002_indexes.sql` — 10 tables have no index.

### Frontend integration fixes (2026-10-15) — `../Niwis Frontend/docs/backend/BACKEND_ISSUES.md`

- [x] **#6 Seasonal change values for 1981–2000 (critical).** Root cause: `climate/breakdown?metric=seasonal-change`
      averaged every month whose year fell in each period's *inclusive* range, guarded only by "is the slice
      non-empty". The database starts on 2000-01-01, so the "1981–2000" slice held the 12 months of **year 2000**
      and p0 was year 2000 alone, labelled as a 20-year climatology (recomputing year 2000 alone gives exactly
      `[21.58, 16.81, 12.66, 17.88]`). Not a fallback, default, extrapolation or template value: real 2000 data,
      mislabelled. The `unavailable` entry was a hard-coded string, never derived from the values, so the two
      disagreed. `var` was also ignored: `var=rainfall` returned the same temperatures in °C.
      Fix: `period_gap` (app/core/aggregate.py) computes a period only when the data cover all of it, and the same
      check produces the `unavailable` entry; `var=rainfall` serves mean seasonal totals (mm) from `4_3`.
- [x] Response checks (`app/core/response_checks.py`), run on every module response (`RESPONSE_CHECKS=log` by
      default, `raise` in tests): a field listed in `unavailable` must be null; no non-null value may be dated outside
      its source tables' first / last record. Audit over every endpoint, key and selector value
      (`app/modules/catalog.py`): `tests/test_response_audit.py` (fixture DB, 5 scopes),
      `tests/integration/test_response_audit_live.py` and `scripts/audit_responses.py` (Supabase / running server /
      recorded responses).
- [x] Audit results. Recorded frontend fixtures (627 responses, pre-fix): 16 violations, all `seasonal-change` (both
      `var`s × 4 scopes: p0 listed unavailable but valued, and dated before 2000). Supabase after the fix: 716
      requests, 0 violations. Fixture DB: also caught `2021–2026` averaged from data that start in 2024 (same bug,
      fixed by `period_gap`), and three empty-scope crashes (500): `climate/table?metric=provinces` (untyped empty
      aggregate), `vegetation/breakdown?metric=drivers` (NaT date), runoff daily-flow series (`discharge`,
      `flood-frequency`, `seasonal`) → now null + reason or a specific 501.

- [x] **#1 `set` ignored on vegetation indices.** Root cause: the shared router passed only `metric` / `layer` to the
      services, and no service read any other parameter, so **every** panel selector the frontend sends was silently
      dropped, not only `set`. Found by the same audit (all selectors in `src/api/liveManifest.ts`): rainfall
      `anomaly?window`, `extremes?kind`, `events?kind`; drought `spi?scale`, `drought-area?scale`,
      `rain-anomaly?window`, `affected?level`; climate `national-trend?var`, `seasonal-change?var`, `heat-days?kind`,
      `extreme-rain?kind`, `indicator-trends?scenario`; vegetation `indices?set`, `index-trend?index`, `drivers?var`;
      runoff `drivers?var`, `top-stations?order`. Fix: `Selector` + `Ctx.select` (app/modules/common.py) validate
      every selector (unknown value → 400 listing the valid ones); each service declares its `SELECTORS`, which the
      audit enumerates. Withheld indices (EVI, SAVI, MSAVI2, VPI, ASI, SVI, NDII) → null series + `unavailable`
      (`index-trend` → 501). `level=catchment` and `scenario=rcp45|rcp85` → 501 (no catchment data / projections;
      `indicator-trends` with a scenario used to return observed values on the projections page). `heat-days?kind=cold`
      = frost nights from `Frost_Day_Flag` (= Tmin < 0 °C in all 52 districts, checked). `tests/test_selectors.py`:
      every value of every selector returns a different response.

### Phase 7 — Hardening (2026-10-14)

- [x] Performance (`docs/PERFORMANCE.md`): summary p95 16–106 ms with warm data caches across 61 scopes per module
      (target < 800 ms); cached repeats ≈ 2 ms; cold first requests 2.6–18 s right after a restart (network to
      eu-west-1, unindexed tables) — background pre-load at startup
- [x] Index-friendly text-date range filters (raw ISO text, no cast); `migrations/proposed/002_indexes.sql` updated
- [x] Security review (`docs/SECURITY_REVIEW.md`): pip-audit clean; fixed unbounded per-window caching in the runoff
      events table and rate-limiter memory growth; security tests (`tests/test_security.py`)
- [x] `docs/FRONTEND_INTEGRATION.md`; README (documents, scripts, adding a module via `router_factory`)
- [x] `docs/VERIFICATION.md`: 21 / 21 checks pass across the five modules
- [x] `docker compose up` from a clean clone: verified
- [x] 380 tests; ruff + mypy (strict) clean

### Open questions / actions for the data team

See `docs/DATA_GAPS.md`. Highest impact: (1) create the read-only role (`001_readonly_api_role.sql`) and the indexes
(`002_indexes.sql`); (2) fix or replace `5.1` vegetation and extend `satellite_remote_sensing` / hydrology beyond
2025-12; (3) fix the placeholder / implausible columns (§3–§4); (4) load data back to 1981 or confirm the baseline;
(5) reload 31 December; (6) flag partial months.

### Phase 6 — Runoff (2026-10-14)

- [x] `app/modules/runoff/`: summary (8 KPIs), 10 time series, by-wma breakdown, 3 tables, 4 map layers, entities;
      501 for station layers / counts, catchment runoff and forecasts
- [x] Volumes and flows derived from depth × real district area (D2); SQL-side scope sums with bind-parameter factors
- [x] `app/core/indices.py`: SSI (gamma per calendar month) with property tests (`tests/test_indices.py`)
- [x] Log-Pearson III flood frequency with bootstrap band; flow-duration curves; downsampling of long daily series
- [x] `docs/VERIFICATION.md`: 4 / 4 runoff values match SQL (incl. real-area volume for uMgungundlovu)
- [x] 33 runoff + 5 index tests (369 total)

### Phase 5 — Vegetation (2026-10-14)

- [x] `app/modules/vegetation/`: summary (8 KPIs), 4 time series, 4 breakdowns, 2 tables, 9 map layers, all from
      `satellite_remote_sensing` (D4); 501 for the 7 indices only in withheld `5.1`, land cover and forecasts
- [x] Drought and vegetation pages return identical values for the same district-month (tested)
- [x] `docs/VERIFICATION.md`: 4 / 4 vegetation values match SQL
- [x] `tests/test_aggregate.py`: unit tests for area weighting, the 95 % coverage rule, class areas, WMA fractions and
      pro-rated normals; 35 vegetation tests (331 total)

### Phase 4 — Climate (2026-10-14)

- [x] `app/modules/climate/`: summary (8 KPIs), 9 time series, 2 breakdowns, 3 tables, 7 map layers; 501 for
      projections, irrigation, risk index, vulnerability and projection tables / maps
- [x] Sen's slope intercept + trend lines (`app/core/stats.py`); national warming +0.30 °C/decade (p = 0.011)
- [x] Withheld 4_4 / 4_5 / 4_6 never appear in any response (tested)
- [x] `docs/VERIFICATION.md`: 4 / 4 climate values match SQL (hot days, ET₀, Sen's slope in SQL, temperature anomaly)
- [x] 35 climate tests (290 total)

### Phase 3 — Drought (2026-10-14)

- [x] `app/modules/drought/`: summary (10 KPIs, per-component dates), 8 time series, severity / province-area
      breakdowns, cascade / affected / municipalities / sources tables, veg-heatmap matrix, 15 map layers (district,
      province); 501 for storage-vs-spi, population, recovery, restrictions, probabilities, early warning, points
      layers, catchment classes and all forecasts
- [x] `app/modules/router_factory.py`: one builder for every module's contract endpoints (rainfall migrated)
- [x] Dam storage = Σ volume / Σ derived capacity (national 63.6 % of 27 846 Mm³, Dec 2025)
- [x] `docs/VERIFICATION.md`: 4 / 4 drought values match SQL (KZN SPI-3, national dam storage, Namakwa VCI and
      soil-moisture anomaly)
- [x] 48 drought tests (255 total)

### Phase 2 — Rainfall (2026-10-14)

- [x] `app/modules/rainfall/`: summary, timeseries (monthly, anomaly, spi-spei, intensity, dry-wet, station),
      forecast (501), breakdown (seasonal, events), tables (provinces, extremes, statistics, sources), 12 map layers
      (+ 3 forecast layers → 501), entities (52 districts) + entity cards
- [x] Shared layers: `app/core/data.py` (canonical-source loaders, SQL-side window aggregation), `aggregate.py`
      (area weights, ≥ 95 % coverage rule, pro-rated normals), `stats.py` (Sen's slope / Mann-Kendall),
      `palettes.py`, `app/modules/common.py` (filters dependency, provenance, response cache)
- [x] Live on Supabase: every endpoint and filter combination returns the expected status; warm cache 3–70 ms,
      cold 2.5–4 s (network round-trips to eu-west-1); startup pre-loads the rainfall frames
- [x] `docs/VERIFICATION.md`: 5 / 5 rainfall values match independent SQL (incl. pro-rated anomaly and the
      area-weighted Western Cape SPI-6) — `python scripts/verify.py rainfall`
- [x] 47 rainfall tests on the fixture DB (207 total); ruff + mypy clean; openapi.json refreshed
- [x] Found: partial current month treated as complete in the DB's anomalies / SPI → DATA_GAPS §14

### District boundaries (2026-10-14)

- [x] Supplied `docs/south_africa_52_areas_clean.geojson` (geoBoundaries ADM2, 32 MB; kept out of git) validated with
      `scripts/check_district_boundaries.py`: 52/52 districts, no overlaps, provinces tiled within 0.15 %, areas
      within 0.2 % of official figures
- [x] `reference/geo/districts.json` (0.8 MB, MDB codes, full-resolution areas) built with
      `scripts/build_districts_geo.py`; app loads it — `/health` reports `areaWeighting: true`
- [x] District–WMA overlap computed (99.8–100 % of each WMA covered); tests added (160 passing)

### Phase 1 — skeleton (2026-10-14)

- [x] `app/config.py`, `app/db.py` (asyncpg pool; READ ONLY + 10 s statement timeout set in SQL on connect and
      re-asserted on release, because Supabase's pooler drops startup parameters — found and fixed in this phase)
- [x] `app/registry/datasets.yaml` + validator: 14 tables, 112 variables, one canonical source per variable;
      duplicates and withheld columns (with reasons) recorded
- [x] Core: thresholds.json (from thresholds.ts, test-checked), districts (52, MDB codes, aliases), filters
      (D1 rules), spatial (equal-area areas; provinces within 0.3 % of official), provenance/staleness, cache, errors
- [x] Endpoints: `/api/v1/health`, `/meta/sources`, `/meta/coverage`, `/meta/variables`, `/reference/*`
- [x] Contract models (`app/schemas/contract.py`) + conformance tests against the zod schemas and examples
- [x] Tests: 151 passing (unit, conformance, API on the recorded fixture DB); 3 integration tests passing against
      Supabase (row counts and asOf equal direct SQL)
- [x] ruff + mypy (strict) clean; Dockerfile (Python 3.12) builds and runs healthy; docs/openapi.json exported
- [x] Docs: CONTRACT_CHANGES.md (new), DATA_GAPS.md (§5 NDVI evidence, §12 boundaries, §13 duplicates),
      DATA_MAPPING.md (decisions applied), README.md

### Decisions applied (2026-10-14)

- D1 districts as entities/map units; WMA = district-overlap (flagged); catchment = DATA_UNAVAILABLE
- D2 real district areas (pending boundaries); runoff volume = depth × real area
- D3 baseline labelled "2000–2026", WMO note
- D4 `satellite_remote_sensing` passes the NDVI plausibility check → single vegetation source for drought and
  vegetation pages; `5.1` withheld entirely
- D5 placeholders / implausible columns withheld (not rescaled)
- Every value carries its own `asOf`; `isStale` after 90 days (configurable); per-component dates for combined panels
- One canonical source per variable; duplicates in DATA_GAPS §13

### Answer to "15 vs 14 tables"

The database holds exactly 14 user tables (all in `public`; no views or other schemas). The `.env` and the build
prompt also list 14 (3 + 6 + 2 + 2 + 1); "15" in the prompt was a miscount. Six names need quoting in SQL
(leading digit, dots, or the space in `4_6_… _master`).

### Phase 0 — discovery (2026-10-14)

- [x] `scripts/inventory.py` → `docs/DB_INVENTORY.md`, `docs/db_inventory.json`
- [x] `scripts/data_checks.sql`, `ndvi_plausibility.sql`, `duplicate_checks.sql` + `scripts/run_checks.py`
- [x] `docs/DATA_MAPPING.md`, `docs/DATA_GAPS.md`, `migrations/proposed/*.sql` (not run)

### Open questions for the data team

See `docs/DATA_GAPS.md` (P1: baseline, placeholder columns, implausible units, `5.1` vegetation, boundaries).

### Environment

- Local: Python 3.13 venv (`.venv`, pip; uv not installed). Docker image: Python 3.12.
- Test DB: `docker compose --profile test up -d testdb` (port 55432, recorded fixture `tests/fixtures/01_fixture.sql.gz`).
