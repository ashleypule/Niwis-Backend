# Progress

## Status: Phase 2 (Rainfall) complete (2026-10-14). Next: Phase 3 — Drought.

### Blocking / waiting on others

- **Read-only role** (data team): API connects as `postgres` via the session pooler; every session is forced READ ONLY
  (verified: writes rejected). Proposed role: `migrations/proposed/001_readonly_api_role.sql`.
- **Indexes** (data team): `migrations/proposed/002_indexes.sql` — 10 tables have no index.

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
