# Progress

## Status: Phase 0 complete — waiting for review of decisions D1–D5 before Phase 1

### Setup (2026-10-14)

- [x] git init, .gitignore (.env, confidential spec, venv/caches)
- [x] reference/API_CONTRACT.md, DATA_DICTIONARY.md, thresholds.ts (pasted by the user, saved verbatim)
- [ ] reference/geo/ (GeoJSON boundaries + SOURCES.md): **not yet provided** — blocks area weights and WMA/catchment (D2)
- [x] .env complete (Supabase URL, keys, session-pooler DB URL). Table lists use the real DB names
      (space in `4_6_… _master`, dots in `5.x` / `6.x`).

### Phase 0 — discovery (2026-10-14)

- [x] `scripts/inventory.py` (read-only session; tested on a throwaway local Postgres first) → `docs/DB_INVENTORY.md`,
      `docs/db_inventory.json`
- [x] `scripts/data_checks.sql` + `scripts/run_checks.py` — targeted consistency checks (37 queries)
- [x] `docs/DATA_MAPPING.md` — every contract key for rainfall, drought, climate, vegetation, runoff
- [x] `docs/DATA_GAPS.md` — for the data team
- [x] `migrations/proposed/001_readonly_api_role.sql`, `002_indexes.sql` — **not run**; for data-team review

### Key facts

- 14 tables, all keyed by **52 district municipalities**; no stations, coordinates, catchment or WMA codes.
- Daily data 2000-01-01 → 2026-09-06; monthly → 2026-09; `satellite_remote_sensing` / `hydrological_drought_master` → 2025-12.
- Stored anomalies use a 2000–2026 per-month mean, not 1981–2010.
- Placeholder / implausible columns listed in DATA_GAPS §3–§4.

### Decisions needed (see DATA_MAPPING §0.4)

- D1 districts as entities + district choropleths instead of stations/grids
- D2 district boundaries for area weighting and WMA/catchment filters (`reference/geo/`)
- D3 baseline labelling (no 1981–2010 data)
- D4 vegetation source per module
- D5 serve or withhold implausible columns

### Credentials

- API connects as `postgres` via the session pooler; every session is set READ ONLY. A dedicated read-only role is
  proposed in `migrations/proposed/001_readonly_api_role.sql` for the data team.

### Environment

- Python 3.13 locally (prompt says 3.12; the Docker image will use 3.12). uv not installed → `.venv` + pip
  (inventory deps in `scripts/requirements-inventory.txt`).
