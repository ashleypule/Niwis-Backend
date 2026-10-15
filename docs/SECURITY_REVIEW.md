# Security review

> Phase 7 review of the NIWIS backend (2026-10-14). Scope: the API code in `app/`, configuration, Docker image and
> the way it uses the shared Supabase database. Each control lists how it is enforced and how it is tested.

## Summary

| Area | Status | Notes |
| --- | --- | --- |
| Database writes | ✅ prevented | Every pooled session is `READ ONLY`, verified on connect and re-asserted on release; writes rejected (tested) |
| Database credentials | ⚠ action for data team | API uses the `postgres` login via the pooler; a dedicated read-only role is proposed (`migrations/proposed/001_readonly_api_role.sql`) |
| SQL injection | ✅ | Values are bind parameters only; identifiers come from the validated registry and are quoted by an allow-list (`app.db.ident`); request values never reach SQL text |
| Secrets | ✅ | `.env` git- and docker-ignored; `SecretStr`; logs scrub connection strings, JWTs and `password=`; responses scanned in tests |
| Error leakage | ✅ | Contract error bodies only; unexpected errors → generic 500 with a request id (details in server logs, scrubbed) |
| Input validation | ✅ | Every filter / key is matched against allow-lists (provinces, WMAs, MDB codes, periods, metric / layer keys); `limit` 1–500; bad input → 400 / 404 |
| CORS | ✅ | Origins from config (defaults: localhost:5173 / 5174); `GET` / `OPTIONS` only; no credentials |
| Rate limiting | ✅ basic | 600 requests / minute / IP (configurable; was 120, too low for a dashboard behind one office NAT); `/health` exempt; trusted networks via `RATE_LIMIT_TRUSTED_CIDRS`; `X-Forwarded-For` only with `TRUST_PROXY_HEADERS=true` (right-most entry), so the IP cannot be spoofed; in-process, memory-bounded; use the gateway or Redis when scaled out; `client_key` hook for per-user limits once auth exists |
| Resource exhaustion | ✅ | 10 s statement timeout; bounded caches (2 048 entries per TTL); large daily slices aggregated or filtered in SQL; long series downsampled to ≤ 2 000 points |
| Authentication | ⏳ hook in place | `get_current_user` on every router allows all requests today; roles can be added without changing the contract |
| Dependencies | ✅ | `pip-audit -r requirements.txt`: no known vulnerabilities (2026-10-14) |
| Container | ✅ | `python:3.12-slim`, non-root user (uid 10001), no secrets baked in (`env_file` at runtime), healthcheck |

## Details

### Read-only database access

Supabase's connection pooler does not forward startup parameters, so `default_transaction_read_only` sent at connect
time was silently ignored (found in Phase 1 via `/health → readOnlySession: false`). Fix (`app/db.py`): each new
connection runs `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` and the statement timeout, then checks
`current_setting('default_transaction_read_only') = 'on'` and refuses the connection otherwise; the pool's release hook
re-asserts both instead of asyncpg's default `RESET ALL`. Tests: `tests/test_api.py::test_sessions_reject_writes`,
`/health` reports `readOnlySession` (integration test against Supabase).

Residual risk: the login itself can write. Recommendation: the data team creates `niwis_api_ro` (SELECT on the 14
tables only, BYPASSRLS or SELECT policies) and the API switches `SUPABASE_DB_URL` to it.

### SQL construction

- Values (dates, district names, weights, thresholds, percentiles) are always `$n` parameters, including arrays
  (`unnest($3::text[], $4::float8[])`).
- Table / column names come only from `app/registry/datasets.yaml`, validated at startup; `ident()` accepts
  `[A-Za-z0-9_. ]{1,63}` and double-quotes the name.
- The only numbers formatted into SQL text are internal constants (rain-day thresholds, the model area). No
  request-derived value is interpolated. Tests: `tests/test_registry.py::test_ident_rejects_injection`,
  `tests/test_security.py::test_injection_shaped_input_is_rejected`.

### Secrets and logging

- `.env` is in `.gitignore` and `.dockerignore`; `.env.example` holds placeholders only. A pre-commit scan for JWTs,
  pooler hosts and connection strings ran before every commit in this project.
- Logs are JSON with request id, method, path, status and duration — never query results or credentials.
  `JsonFormatter` scrubs messages and tracebacks (`tests/test_security.py`).
- Responses never contain connection strings or keys (`tests/test_api.py::test_responses_contain_no_secrets`).

### Findings fixed during the review

| Finding | Fix |
| --- | --- |
| Read-only startup parameter ignored by the pooler | SQL-level read-only + verification (above) |
| `events` table cached a daily slice per arbitrary `from`/`to` window (memory growth up to ~1 GB under abuse) | Exceedances filtered in SQL (`DataAccess.daily_exceedances`, ≤ 5 000 rows); the raw daily loader was removed |
| Rate-limiter map grew with every client IP | Idle clients pruned once the map exceeds 10 000 entries |
| Text-date range filters cast the column (no index use, full scans) | Raw ISO-text comparison (`Dataset.range_sql`), index-friendly |

### Recommendations for deployment

1. Create the read-only role and switch the API to it (data team).
2. Serve over HTTPS behind a reverse proxy / API gateway; set `CORS_ORIGINS` to the production frontend origin only.
3. Put rate limiting at the gateway (per client and global) when running more than one instance.
4. When logins arrive, implement token verification in `app/auth.py::get_current_user` (the header is already
   accepted and never logged).
5. Rotate the service-role key (it was shared in chat during setup; the API does not use it — only the inventory
   script's REST fallback does).
