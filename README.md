# NIWIS backend

API for the **National Integrated Water Information System** (Department of Water and Sanitation, South Africa). It
serves the frontend contract (`reference/API_CONTRACT.md`) from the NIWIS Supabase database, **read-only**.

- Python 3.12 · FastAPI · asyncpg · Pydantic v2
- Interactive docs: `http://localhost:8000/docs` · schema: `docs/openapi.json`
- Status of modules and data: `PROGRESS.md` · contract additions: `docs/CONTRACT_CHANGES.md`
- **Live modules:** `rainfall`, `drought`, `climate`, `vegetation`, `runoff` — what each serves and what is unavailable:
  `docs/FRONTEND_INTEGRATION.md`

| Document | Contents |
| --- | --- |
| `docs/DB_INVENTORY.md` | Profile of the 14 database tables |
| `docs/DATA_MAPPING.md` | Every contract key → source table / columns / method, or unavailable |
| `docs/DATA_GAPS.md` | What the database lacks or gets wrong — for the data team |
| `docs/CONTRACT_CHANGES.md` | Backwards-compatible additions to the API contract |
| `docs/FRONTEND_INTEGRATION.md` | How to switch the frontend from mock data to this API |
| `docs/VERIFICATION.md` | API values checked against independent SQL (21 checks) |
| `docs/PERFORMANCE.md` | Summary-endpoint latency |
| `docs/SECURITY_REVIEW.md` | Security controls, findings and recommendations |
| `docs/openapi.json` | OpenAPI schema (also served at `/openapi.json`) |

## Quick start

```bash
cp .env.example .env              # fill in the four Supabase values (never commit .env)

# with uv
uv sync --group dev
uv run uvicorn app.main:app --reload

# or with pip
python -m venv .venv && . .venv/Scripts/activate      # Windows; on Linux/macOS: . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload

# or with Docker
docker compose up --build         # http://localhost:8000
```

Check it works: `GET /api/v1/health` should report `"status": "ok"` and `"readOnlySession": true`.

## Configuration (`.env`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `SUPABASE_DB_URL` | — | Postgres connection string (Supabase → Connect → **Session pooler**). Required for data. |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | — | Only used by the inventory script's REST fallback. |
| `NIWIS_SUPABASE_TABLES_MODULE_<n>` | — | Tables the data team has registered; a test checks they match `app/registry/datasets.yaml`. |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:5174` | Comma-separated frontend origins. |
| `STALE_AFTER_DAYS` | `90` | A value is `isStale` when its `asOf` lags the newest DB date by more than this. |
| `PREWARM_CATEGORIES` | `true` | Pre-load every category chart group (national, 12M) at startup and before its cache expires; at most `DB_POOL_MAX − 1` queries at once. |
| `DROUGHT_CLASS_AREA_PCT` | `20` | Headline drought class = the worst class that, with all worse classes, covers at least this % of the area (spec §3.4 classes per district). |
| `RATE_LIMIT_PER_MINUTE` | `600` | Per client IP; `0` disables. `/api/v1/health` is exempt. |
| `RATE_LIMIT_TRUSTED_CIDRS` | *(empty)* | Comma-separated networks that are never limited (e.g. the DWS network). |
| `TRUST_PROXY_HEADERS` | `false` | Read the client IP from `X-Forwarded-For` (right-most entry). Only behind a reverse proxy that sets it. |
| `RESPONSE_CHECKS` | `log` | Response invariants (`app/core/response_checks.py`): `log`, `raise` (tests / debugging), `off`. |
| `DB_POOL_MAX`, `DB_STATEMENT_TIMEOUT_MS` | `5`, `10000` | Pool size and per-statement timeout. |
| `CACHE_TTL_SUMMARY_S`, `CACHE_TTL_MAP_S`, `CACHE_TTL_LONG_SERIES_S`, `CACHE_TTL_META_S` | 900, 900, 3600, 3600 | Cache lifetimes. |
| `LOG_LEVEL` | `INFO` | JSON logs to stdout; secrets are scrubbed. |

## Safety rules built in

- **Read-only database access.** Every pooled connection is set to `READ ONLY` with a statement timeout; the setting is
  verified on connect and re-asserted when a connection returns to the pool (Supabase's pooler ignores startup
  parameters, so this is done in SQL). `/api/v1/health` reports `readOnlySession`. Nothing in this repo writes to the
  database; proposed indexes and a read-only role are in `migrations/proposed/` for the data team.
- **No string-built values.** Values are always bind parameters. Table/column names come only from the validated
  registry and are quoted by `app.db.ident()`, which rejects anything unexpected.
- **No invented data.** Missing or withheld data return `501 DATA_UNAVAILABLE` (whole panel) or `null` plus an
  `unavailable` entry (single field) — see `docs/CONTRACT_CHANGES.md` §3.
- **Secrets** live only in `.env` (git-ignored) and are never logged or returned.

## Tests and checks

```bash
docker compose --profile test up -d testdb    # local Postgres with the recorded fixture (tests/fixtures)
ruff check . && ruff format --check .
mypy
pytest                                        # unit, contract-conformance and API tests (fixture DB)
pytest -m integration                         # opt-in, read-only checks against the real Supabase DB
python scripts/export_openapi.py              # refresh docs/openapi.json (a test fails if it is stale)
python scripts/verify.py                      # API values vs independent SQL on Supabase → docs/VERIFICATION.md
python scripts/audit_responses.py             # every endpoint × selector × scope vs app/core/response_checks
python scripts/bench.py                       # summary latency on Supabase → docs/PERFORMANCE.md
python scripts/inventory.py                   # re-profile the database → docs/DB_INVENTORY.md
python -m pip_audit -r requirements.txt       # dependency vulnerability check
```

`tests/test_contract_conformance.py` checks every response model field-by-field against the zod schemas in
`reference/API_CONTRACT.md` and validates every example payload. API tests without the fixture DB are skipped.

## Layout

```
app/
  main.py                 FastAPI app, CORS, error handlers, routers
  config.py               settings (.env)
  db.py                   read-only asyncpg pool, identifier quoting
  registry/datasets.yaml  ← every table, column, variable, unit, canonical / duplicate / withheld role
  registry/registry.py    loads + validates the registry
  core/                   thresholds (thresholds.json ← reference/thresholds.ts), districts, filters,
                          spatial (areas, WMA overlap), provenance/staleness, cache, errors
  meta/                   /health, /meta/sources, /meta/coverage, /meta/variables
  refdata/                /reference/{provinces|wmas|municipalities|catchments}
  modules/                one package per contract module (rainfall, drought, climate, vegetation, runoff)
  modules/router_factory.py  builds the contract endpoints for a module from its service class
  schemas/contract.py     Pydantic mirrors of the contract's zod schemas
scripts/                  inventory, data checks, fixture builder, boundary builder, verify, bench, OpenAPI export
migrations/proposed/      SQL for the data team to review — never run by this project
reference/                inputs copied from the frontend (contract, data dictionary, thresholds, geo)
docs/                     inventory, mapping, gaps, contract changes, verification, openapi.json
```

## Adding a new module

Example: Dam & Reservoir Status (`dams`, spec §8) once the data team loads per-dam tables.

1. **Register the tables** in `app/registry/datasets.yaml`: `db_module`, `module: dams`, `serves: [dams]`, the
   `location` and `time` columns, and every column with its variable (add new variables to the `variables:` catalogue
   with label, contract unit and Data Dictionary metric) and role (`canonical`, `duplicate`, `withheld` + reason,
   `unused`). Run `pytest tests/test_registry.py` — the registry refuses duplicates without a canonical source,
   withheld columns without a reason, and unsafe identifiers.
2. **Add the table names** to `.env` / `.env.example` as `NIWIS_SUPABASE_TABLES_MODULE_6=…`.
3. **Profile it**: `python scripts/inventory.py` refreshes `docs/DB_INVENTORY.md`; add mapping rows to
   `docs/DATA_MAPPING.md` and any problems to `docs/DATA_GAPS.md`.
4. **Add `app/modules/dams/`**: a `service.py` class with `summary()`, `timeseries(metric)`, `forecast(metric)`,
   `breakdown(metric)`, `table(metric, limit)`, `map(layer)` (and `matrix`, `entities`, `entity` if the contract
   has them), and a two-line `router.py`: `router = build_router("dams", DamsService, entities=True)`. Read data
   through `ctx.data` (`DataAccess`: canonical-source loaders by variable id, SQL-side aggregation), aggregate with
   `app/core/aggregate.py` (area weights, 95 % coverage rule, sums over a scope), and raise `DataUnavailable` /
   `unknown(...)` for keys the DB cannot serve / the contract does not define. Set `asOf`, `source`, `isStale` via
   `ctx.provenance(...)`. The rainfall module is the reference implementation.
5. **List it** in `IMPLEMENTED_MODULES` (`app/modules/__init__.py`), include the router and its `prewarm` in
   `app/main.py`; `/health` then reports it as `live`.
6. **Test**: rebuild the fixture (`python scripts/make_fixture.py`), add API tests, run
   `python scripts/export_openapi.py`, and add ≥ 3 `Check(...)` entries to `scripts/verify.py`
   (`python scripts/verify.py dams` writes them to `docs/VERIFICATION.md`).

## Data notes

All tables are keyed by the **52 district municipalities** (no stations or coordinates); province, national and WMA
values are area-weighted from districts once official district boundaries are supplied (`docs/DATA_GAPS.md` §12).
Anomalies use the database's 2000–2026 baseline, shorter than the WMO 30-year standard. Details:
`docs/DATA_MAPPING.md`, `docs/DATA_GAPS.md`.
