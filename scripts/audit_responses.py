"""Audit API responses with app.core.response_checks.

    python scripts/audit_responses.py                       # every endpoint, in-process on the database in .env
    python scripts/audit_responses.py --base http://localhost:8000   # every endpoint on a running server
    python scripts/audit_responses.py --recorded "../Niwis Frontend/tests/fixtures/backend"

Endpoints come from app.modules.catalog (every module, key and selector value) in four scopes: national, a province,
a district and a WMA (the frontend's fixture scopes). `--recorded` checks a directory of recorded responses
(`{"status", "body"}` files, as written by the frontend's `npm run fixtures:record`), with source coverage taken from
the recorded `meta/sources` response. Exit code 1 when any response fails a check.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import Settings  # noqa: E402
from app.core.response_checks import Coverage, all_violations  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules import catalog  # noqa: E402

SCOPES = [{}, {"province": "WC"}, {"municipality": "DC32"}, {"wma": "WMA5"}]


def coverage_from(meta_sources: dict[str, Any]) -> Coverage:
    return {
        it["table"]: (dt.date.fromisoformat(it["firstDate"]), dt.date.fromisoformat(it["asOf"]))
        for it in meta_sources["items"]
        if it.get("firstDate") and it.get("asOf")
    }


def audit_recorded(directory: Path) -> list[str]:
    files = [p for p in sorted(directory.glob("*.json")) if p.name != "index.json"]
    meta = next((p for p in files if p.name.startswith("meta_sources")), None)
    coverage = coverage_from(json.loads(meta.read_text(encoding="utf-8"))["body"]) if meta else None
    problems = []
    for p in files:
        f = json.loads(p.read_text(encoding="utf-8"))
        if f.get("status") == 200:
            problems += [f"{p.name}: {v}" for v in all_violations(f.get("body"), coverage)]
    print(f"{len(files)} recorded responses checked")
    return problems


async def audit_client(client: httpx.AsyncClient) -> list[str]:
    coverage = coverage_from((await client.get("/api/v1/meta/sources")).json())
    n, problems = await catalog.audit(client, SCOPES, coverage)
    print(f"{n} requests checked")
    return problems


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="audit a running server, e.g. http://localhost:8000")
    ap.add_argument("--recorded", type=Path, help="audit a directory of recorded responses")
    args = ap.parse_args()
    if args.recorded:
        problems = audit_recorded(args.recorded)
    elif args.base:
        async with httpx.AsyncClient(base_url=args.base, timeout=300) as c:
            problems = await audit_client(c)
    else:
        app = create_app(Settings(rate_limit_per_minute=0, response_checks="off", log_level="WARNING"))
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://audit", timeout=300) as c,
        ):
            problems = await audit_client(c)
    for p in problems:
        print(p)
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
