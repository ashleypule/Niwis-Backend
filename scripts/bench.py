"""Latency of the summary endpoints against the live database (read-only), written to docs/PERFORMANCE.md.

Three measurements per module:
- cold: first national summary after startup, before the background pre-load has finished;
- warm data / new scope: one request per province (9) and district (52) — each response is computed from the cached
  data frames (the response cache does not help: every scope is a new key) — p50 / p95 / max;
- warm response: the same request repeated (served from the response cache).

Usage: python scripts/bench.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import Settings  # noqa: E402
from app.core import districts  # noqa: E402
from app.main import _warm, create_app  # noqa: E402

MODULES = ["rainfall", "drought", "climate", "vegetation", "runoff"]
OUT = ROOT / "docs" / "PERFORMANCE.md"


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


async def main() -> None:
    app = create_app(Settings(rate_limit_per_minute=0, log_level="WARNING"))
    rows = []
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://b", timeout=300) as c,
    ):
        cold = {}
        for m in MODULES:
            t0 = time.perf_counter()
            (await c.get(f"/api/v1/{m}/summary")).raise_for_status()
            cold[m] = (time.perf_counter() - t0) * 1000
        await _warm(app.state.niwis)  # make sure every pre-load has completed
        scopes = [{"province": p} for p in districts.PROVINCES] + [
            {"municipality": d.code} for d in districts.districts()
        ]
        for m in MODULES:
            times = []
            for params in scopes:
                t0 = time.perf_counter()
                r = await c.get(f"/api/v1/{m}/summary", params=params)
                r.raise_for_status()
                times.append((time.perf_counter() - t0) * 1000)
            rep = []
            for _ in range(20):
                t0 = time.perf_counter()
                (await c.get(f"/api/v1/{m}/summary")).raise_for_status()
                rep.append((time.perf_counter() - t0) * 1000)
            rows.append((m, cold[m], statistics.median(times), pct(times, 95), max(times), pct(rep, 95)))
            print(
                f"{m:11s} cold {cold[m]:8.0f} ms | new scope p50 {rows[-1][2]:6.0f} p95 {rows[-1][3]:6.0f} "
                f"max {rows[-1][4]:6.0f} ms | cached p95 {rows[-1][5]:5.1f} ms"
            )
    lines = [
        "# Performance — summary endpoints\n",
        "> Measured with `python scripts/bench.py`: in-process app against the live Supabase database (eu-west-1) from",
        "> the development machine. Target: p95 < 800 ms on summary endpoints with a warm cache.\n",
        "| Module | Cold (first request) | Warm data, new scope p50 | p95 | max | Warm response p95 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for m, cd, p50, p95, mx, rp in rows:
        lines.append(f"| `{m}` | {cd:,.0f} ms | {p50:,.0f} ms | **{p95:,.0f} ms** | {mx:,.0f} ms | {rp:.1f} ms |")
    lines += [
        "",
        '"New scope" = one request for each of the 9 provinces and 52 districts after the data caches are warm; each is',
        'computed from cached frames (no response-cache hit). "Warm response" = the same request repeated.',
        "",
        "**Cold requests** are dominated by network round-trips to the database (≈ 0.5 s each from South Africa to",
        "eu-west-1) and full-table aggregations on tables without indexes. The app pre-loads every module's data in the",
        "background at startup, so in practice only the first seconds after a restart are cold. The proposed indexes",
        "(`migrations/proposed/002_indexes.sql`) and hosting the API in the same region as the database would cut cold",
        "times further.",
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
