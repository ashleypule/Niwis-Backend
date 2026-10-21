# Contributing to the NIWIS backend

Thank you for helping. NIWIS is the Department of Water and Sanitation's public water information system; this
repository is its read-only API. The frontend lives in the companion repository (`Niwis-Frontend`).

## Ground rules

These are not style preferences: a change that breaks one is not merged.

1. **The database is read-only.** No writes, no DDL, no migrations run by the API or its scripts. Database changes
   are proposals in `migrations/proposed/` for the data team. Sessions are forced `READ ONLY` (`app/db.py`); keep it so.
2. **Never invent numbers.** A value the database cannot provide is `501 DATA_UNAVAILABLE` (a whole panel) or `null`
   with an `unavailable` reason (one field) — never a placeholder, a default or a sample (`docs/CONTRACT_CHANGES.md` §3).
3. **Values that are provably impossible are withheld, with the reason** (constant in every row, identical across
   districts, dated in the future or before the instrument existed, wrong unit). Run `scripts/constancy_check.py` and
   the plausibility checks on any new table, and record what you withhold in `docs/DATA_GAPS.md`.
4. **Rows dated after today are never served.**
5. **Labels and units come from the database catalogue** (`app/registry/catalogue.yaml`: category = table,
   variable = column); one unit per chart axis.
6. **SQL values are bind parameters; identifiers come only from the registry** and are quoted with `app.db.ident()`.
   Nothing from a request is ever written into SQL text.
7. **No secrets in the repository** — not in code, tests, fixtures, docs or CI workflows. `.env` is git-ignored.
8. A decision on unclear data is written down in `docs/DECISIONS.md`: the decision, the evidence and the alternative
   considered.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate          # Windows: . .venv/Scripts/activate
pip install -r requirements-dev.txt
docker compose --profile test up -d testdb            # local Postgres with the recorded fixture (tests/fixtures)
cp .env.example .env                                  # only needed to run against the real database
```

No database credentials are needed for the test suite: it runs against the fixture database.

## Before you open a pull request

```bash
ruff check . && ruff format --check .
mypy
pytest                                                # unit, contract-conformance and API tests (fixture DB)
python scripts/gen_readonly_role.py --check           # the read-only role still covers every registered table
```

The same checks run in CI (`.github/workflows/ci.yml`) on every push and pull request, without secrets or a database
connection. If you have read access to the real database, also run `pytest -m integration` and
`python scripts/verify.py` (API values against independent SQL; results in `docs/VERIFICATION.md`).

## Common changes

- **A new table:** register it in `app/registry/datasets.yaml`, rebuild the catalogue (`python
  scripts/build_catalogue.py`), profile it (`python scripts/inventory.py --append`), run the constancy and plausibility
  checks, regenerate the read-only role (`python scripts/gen_readonly_role.py`) and add a fixture slice
  (`scripts/make_fixture.py`) so its tests run offline.
- **A new endpoint or field:** keep the frontend contract (`reference/API_CONTRACT.md`) backwards compatible and
  describe additions in `docs/CONTRACT_CHANGES.md`; `docs/openapi.json` is refreshed with `python
  scripts/export_openapi.py` (a test fails when it is stale).
- **A new module:** see "Adding a new module" in `README.md`.

## Commits and pull requests

Small, focused commits with a message that says what changed and why. Describe in the pull request how you verified
the change (tests, SQL spot-checks). By contributing you agree that your contribution may be published under the
licence DWS confirms for this project (see `README.md`, "Licence").
