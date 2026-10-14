# NIWIS Backend — Plan

Build order (from the build prompt). ✅ done · ▶ next · ⏸ blocked

0. ✅ Discovery → docs/DB_INVENTORY.md, DATA_MAPPING.md, DATA_GAPS.md. Reviewed; decisions D1–D5 answered 2026-10-14.
1. ✅ Skeleton: config, read-only DB pool, registry (canonical sources), health + meta + reference endpoints, contract
   models + conformance tests, fixture DB, Docker.
2. ✅ Rainfall (Module 4): summary, timeseries, breakdown, table, map, entities, forecast (unavailable); verify 3 values
   by SQL in docs/VERIFICATION.md.
3. ✅ Drought (Module 1) — per-component asOf (cascade, drought class).
4. ✅ Climate (Module 2).
5. ✅ Vegetation (Module 3) — from `satellite_remote_sensing` (D4).
6. ✅ Runoff (Module 5) — derived volume = depth × real district area.
7. ✅ Hardening: performance pass, proposed indexes, security review, README / VERIFICATION / CONTRACT_CHANGES,
   `docker compose up` from a clean clone, docs/FRONTEND_INTEGRATION.md.

✅ District boundaries supplied and validated (2026-10-14): area-weighted province / national / WMA aggregates are
unblocked (`reference/geo/districts.json`).

Ground rules: database read-only; secrets stay out of git; never invent data; commit after each phase.
