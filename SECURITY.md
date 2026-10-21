# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Report it privately through GitHub's private
vulnerability reporting: on this repository, open **Security → Report a vulnerability**. Include what you found, how to
reproduce it and its impact. You will get an acknowledgement, and a fix or an explanation, as soon as possible.

If you believe a credential has been exposed, say so in the first line of the report: credentials are rotated first
and investigated after.

## What the API is, and what it does to stay safe

NIWIS is a **public, open-source** system with **no login**: every endpoint is an anonymous, read-only `GET` that
serves public data. The protections are therefore about keeping the shared database safe and the service available:

| Control | Where |
| --- | --- |
| Database sessions forced `READ ONLY`, verified on connect and on every return to the pool; 10 s statement timeout | `app/db.py` |
| A dedicated read-only database role (SELECT on the registered tables only), required before `ENV=production` starts | `migrations/proposed/001_readonly_api_role.sql`, `DEPLOYMENT.md` |
| No SQL built from requests: values are bind parameters, identifiers come from the validated registry | `app/db.py`, `app/registry/` |
| Allow-listed inputs (regions, codes, periods, metric / layer keys); bad input → 400 / 404 | `app/core/filters.py`, module routers |
| Rate limit 600 requests / minute per client IP; `X-Forwarded-For` only from configured proxies | `app/middleware.py` |
| Request size limits (413 / 414), GET / OPTIONS only | `app/middleware.py`, `app/main.py` |
| Security headers (CSP, nosniff, frame denial, referrer policy; HSTS in production) | `app/middleware.py` |
| No stack traces or internal messages in responses; secrets scrubbed from logs | `app/main.py`, `app/logging_config.py` |
| No secrets in the repository; full git history scanned with gitleaks (no leaks, 2026-10-21) | `.gitleaks.toml`, CI |
| Dependencies audited (`pip-audit`: no known vulnerabilities, 2026-10-21) | CI |

More detail: `docs/SECURITY_REVIEW.md` and `docs/DECISIONS.md` (D23–D25).

## Supported versions

Only the `master` branch is maintained; fixes are made there.
