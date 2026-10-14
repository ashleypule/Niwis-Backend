# Progress

## 2026-10-14 — Setup

- [x] git init, .gitignore (.env, confidential spec)
- [x] reference/API_CONTRACT.md, DATA_DICTIONARY.md, thresholds.ts (pasted by the user in chat, saved verbatim)
- [ ] reference/geo/ (GeoJSON boundaries + SOURCES.md): **not yet provided**
- [ ] .env with SUPABASE_* and NIWIS_SUPABASE_TABLES_MODULE_* : **not yet provided** (blocks Phase 0)
- [x] scripts/inventory.py written and tested against a throwaway local Postgres (Docker): read-only session,
      time resolution/gaps, per-location coverage, wide/long detection, text dates, indexes/PK, REST fallback
- [ ] .env: file exists but SUPABASE_URL / keys / SUPABASE_DB_URL are still placeholders (blocks Phase 0 run)
- [ ] Phase 0: run inventory, write DB_INVENTORY / DATA_MAPPING / DATA_GAPS

Environment notes: Python 3.13 locally (prompt says 3.12; Docker image will use 3.12). uv not installed; using .venv + pip (inventory deps in scripts/requirements-inventory.txt).
