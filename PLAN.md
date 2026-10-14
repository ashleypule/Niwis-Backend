# NIWIS Backend — Plan

Build order (from the build prompt):

0. Discovery → docs/DB_INVENTORY.md, DATA_MAPPING.md, DATA_GAPS.md. **Stop for review.**
1. Skeleton: config, DB pool, registry, health + meta endpoints, tests, Docker.
2. Rainfall (Module 4)
3. Drought (Module 1)
4. Climate (Module 2)
5. Vegetation (Module 3)
6. Runoff (Module 5)
7. Hardening: performance, proposed indexes, security review, docs, FRONTEND_INTEGRATION.md.

Ground rules: the database is read-only; secrets stay out of git; never invent data; commit after each phase.
