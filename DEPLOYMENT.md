# Deploying NIWIS

NIWIS runs as two containers: the **API** (this repository) and the **web** container (the frontend repository's
`Dockerfile`: the production build served by nginx, which also proxies `/api` and `/docs` to the API). The database is
the NIWIS Supabase project, which the API only ever reads.

```
browser ──TLS──▶ load balancer / TLS proxy ──▶ web (nginx :8080) ──/api, /docs──▶ api (uvicorn :8000) ──▶ Supabase (read-only role)
```

## 1. Before the first deployment: the read-only database role

The API must not connect as `postgres` in production. A database owner (the data team) runs, once:

```bash
psql "<owner connection string>" -v ON_ERROR_STOP=1 \
     -v ro_password="$(read -rsp 'niwis_api_ro password: ' p; echo "$p")" \
     -f migrations/proposed/001_readonly_api_role.sql
```

It creates `niwis_api_ro` with SELECT on the 36 registered tables only, `default_transaction_read_only = on`, and one
SELECT policy per table (every table has row-level security enabled). It is idempotent; re-run it (after
`python scripts/gen_readonly_role.py`) whenever a table is registered. Then:

- set `SUPABASE_DB_URL` to the **session pooler** string for user `niwis_api_ro.<project-ref>`;
- set `DB_READONLY_ROLE_CONFIRMED=true`.

With `ENV=production` the API **refuses to start** until `DB_READONLY_ROLE_CONFIRMED=true`. Check afterwards:
`GET /api/v1/health` → `"readOnlySession": true`.

## 2. Configuration

The API reads `.env` in the repository root (never committed; start from `.env.example`). Production values:

| Variable | Production value | Notes |
| --- | --- | --- |
| `SUPABASE_DB_URL` | session-pooler URL of `niwis_api_ro` | Required. |
| `ENV` | `production` | Set by `deploy/docker-compose.prod.yml`. Enables HSTS and the read-only-role check. |
| `DB_READONLY_ROLE_CONFIRMED` | `true` | Only after step 1. |
| `CORS_ORIGINS` | the public site's origin, e.g. `https://niwis.dws.gov.za` | Empty allows `*` (GET only, no credentials). Same-origin deployments behind the web container need no CORS at all. |
| `TRUST_PROXY_HEADERS`, `TRUSTED_PROXY_CIDRS` | `true`, `172.30.0.0/24` | Set by the compose file: only nginx's `X-Forwarded-For` is believed. |
| `RATE_LIMIT_PER_MINUTE` | `600` | Per client IP. `RATE_LIMIT_TRUSTED_CIDRS` exempts networks (e.g. a monitoring host). |
| `DB_POOL_MAX`, `DB_STATEMENT_TIMEOUT_MS` | `5`, `10000` | Keep the pool small: the Supabase pooler is shared. |
| `CACHE_TTL_*_S` | defaults | Also the `Cache-Control: max-age` the API sends. |
| `PREWARM_CATEGORIES`, `PREWARM_SCOPES` | defaults | Keep warm: cold category charts can approach the statement timeout. |
| `LOG_LEVEL` | `INFO` | JSON logs on stdout; secrets are scrubbed. |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | leave **unset** | Only the inventory script's REST fallback uses them; the service-role key must never be on the server. |

The frontend needs no runtime configuration: `npm run build:release` (`.env.release`) builds it in `api` mode against
the same origin, with no sample data. Compose variables: `FRONTEND_DIR` (path to the frontend checkout, default
`../../Niwis-Frontend`), `NIWIS_HTTP_PORT` (default `80`), `NIWIS_ENV` (default `production`).

## 3. Start the stack

```bash
git clone https://github.com/ashleypule/Niwis-Backend.git
git clone https://github.com/ashleypule/Niwis-Frontend.git
cd Niwis-Backend && cp .env.example .env    # fill in as in section 2
cd deploy
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps   # both services "healthy"
```

The web container listens on port 80 (plain HTTP): terminate TLS in front of it (load balancer, Caddy, Traefik or an
nginx with certificates) and forward to it. Only the web container publishes a port; the API is reachable only on the
compose network. Both containers run as non-root users with read-only filesystems.

Update to a new version: `git pull` in both checkouts, then `docker compose -f docker-compose.prod.yml up -d --build`.
The API warms its caches in the background after a restart (about a minute for the module frames, longer for every
category chart); requests made meanwhile are served, only slower.

## 4. Health checks

| Check | What it means |
| --- | --- |
| `GET /healthz` (web) | nginx is up. Used by the web container's health check. |
| `GET /api/v1/health` | The API is up; `database.reachable`, `readOnlySession` and the module statuses (`live`, `partial`, `unavailable`). Used by the API container's health check and exempt from rate limiting. |
| `GET /api/v1/meta/coverage` | Newest data date per module: use it to alert when the data team's loads stop. |

## 5. What nginx does

`deploy/nginx.conf` in the frontend repository:

- **gzip** for HTML, CSS, JS, JSON, GeoJSON and SVG;
- **cache headers**: fingerprinted `/assets/*` `public, max-age=31536000, immutable`; `/geo` and `/brand` one day;
  `index.html` (every app route) `no-cache`, so a release shows at once; `/api` keeps the API's own `Cache-Control`
  (`public, max-age=` its cache lifetime, `no-store` for health and errors);
- **security headers**: a Content Security Policy that allows only same-origin scripts (plus the hash of the inline
  theme script), CARTO map tiles and same-origin API calls; `nosniff`, `X-Frame-Options: DENY`, referrer and
  permissions policies. The API sets its own stricter headers on `/api` and the Swagger CSP on `/docs`;
- `client_max_body_size 16k` (the API only takes GET requests); `/docs` stays public.

## 6. Logs

Both containers log to stdout; the compose file keeps 5 × 10 MB per container (`json-file`).

```bash
docker compose -f docker-compose.prod.yml logs -f api     # JSON lines: request_id, path, status, ms
docker compose -f docker-compose.prod.yml logs -f web     # nginx access / error log
```

Every API response carries `X-Request-Id` (nginx passes its `$request_id`); error responses include it, so a user's
report can be matched to the log line. Ship the logs to the department's log platform with the Docker logging driver of
your choice.

## 7. Rebuilding the catalogue after a database change

Labels, units and chart groups come from `app/registry/catalogue.yaml`, generated from the database schema and the
registry. When the data team adds or changes a table:

```bash
# 1. register the table in app/registry/datasets.yaml (see CONTRIBUTING.md, "A new table")
python scripts/build_catalogue.py            # regenerate the catalogue from Supabase (read-only)
python scripts/inventory.py --append         # profile it into docs/DB_INVENTORY.md
python scripts/constancy_check.py --tables "<table>"   # and the plausibility checks: withhold impossible values
python scripts/gen_readonly_role.py          # extend the read-only role; the data team re-runs it (section 1)
pytest && python scripts/verify.py           # tests, then API values against independent SQL
```

Commit, then redeploy (section 3). `python scripts/build_catalogue.py --check` fails when the committed catalogue is out
of date with the database (it reads the schema, so it needs `SUPABASE_DB_URL`).

## 8. Hosting notes

The Supabase project is hosted in AWS `eu-west-1` (Ireland). That is a hosting choice, not a POPIA blocker: the
database holds aggregated environmental data per district and no personal information (`docs/DECISIONS.md` D27).
Reassess the region if personal data are ever added.
