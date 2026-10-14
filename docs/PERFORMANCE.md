# Performance — summary endpoints

> Measured with `python scripts/bench.py`: in-process app against the live Supabase database (eu-west-1) from
> the development machine. Target: p95 < 800 ms on summary endpoints with a warm cache.

| Module | Cold (first request) | Warm data, new scope p50 | p95 | max | Warm response p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `rainfall` | 15,406 ms | 28 ms | **33 ms** | 38 ms | 2.2 ms |
| `drought` | 8,198 ms | 27 ms | **30 ms** | 40 ms | 2.8 ms |
| `climate` | 18,166 ms | 69 ms | **106 ms** | 131 ms | 2.0 ms |
| `vegetation` | 2,598 ms | 14 ms | **16 ms** | 60 ms | 1.9 ms |
| `runoff` | 7,798 ms | 42 ms | **47 ms** | 52 ms | 2.7 ms |

"New scope" = one request for each of the 9 provinces and 52 districts after the data caches are warm; each is
computed from cached frames (no response-cache hit). "Warm response" = the same request repeated.

**Cold requests** are dominated by network round-trips to the database (≈ 0.5 s each from South Africa to
eu-west-1) and full-table aggregations on tables without indexes. The app pre-loads every module's data in the
background at startup, so in practice only the first seconds after a restart are cold. The proposed indexes
(`migrations/proposed/002_indexes.sql`) and hosting the API in the same region as the database would cut cold
times further.
