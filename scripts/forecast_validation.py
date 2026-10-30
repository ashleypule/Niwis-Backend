"""Backtest every page forecast variable on the real database and write docs/FORECAST_VALIDATION.md.

    python scripts/forecast_validation.py                 # national, horizons 24 (as the pages) and 3 months
    python scripts/forecast_validation.py --only spi-3    # one variable

Read-only (SUPABASE_DB_URL from .env). Each variable's national series is built exactly as the API builds it
(app.forecasting.series), then every model runs through the same rolling-origin backtest as a Studio "Compare all"
run (experimental models included). No parameter is tuned for these results. Auto's pick = the lowest backtest MASE
among the non-experimental models, which is what the pages show.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.core.cache import MemoryCache  # noqa: E402
from app.core.data import DataAccess  # noqa: E402
from app.core.filters import parse_scope  # noqa: E402
from app.core.spatial import get_spatial  # noqa: E402
from app.db import Database  # noqa: E402
from app.forecasting.engine import RunSpec, run  # noqa: E402
from app.forecasting.pages import VARS  # noqa: E402
from app.forecasting.registry import MODELS  # noqa: E402
from app.forecasting.series import build_series  # noqa: E402
from app.meta.service import MetaService  # noqa: E402
from app.registry.registry import get_registry  # noqa: E402
from app.state import AppState  # noqa: E402

DOC = ROOT / "docs" / "FORECAST_VALIDATION.md"
RAW = ROOT / "docs" / "forecast_validation.json"


async def series_for(only: set[str] | None) -> dict[str, Any]:
    s = Settings(prewarm_categories=False)
    db = Database(s)
    await db.connect()
    reg, cache = get_registry(), MemoryCache()
    st = AppState(s, db, reg, cache, MetaService(db, reg, cache, s), get_spatial(), DataAccess(db, reg, cache, s))
    out: dict[str, Any] = {}
    try:
        for vid, v in VARS.items():
            if only and vid not in only:
                continue
            t = v.make(reg)
            sd = await build_series(st, t, parse_scope(), "monthly")
            out[vid] = (v, t, sd)
    finally:
        await db.close()
    return out


def one(vid: str, v: Any, t: Any, sd: Any, horizon: int, model: str) -> dict[str, Any]:
    low, high = t.bounds(float(sd.values.min())) if not sd.values.empty else (None, None)
    spec = RunSpec("monthly", horizon, model, classify=t.classify, clip_low=low, clip_high=high)
    t0 = time.monotonic()
    try:
        r = run(sd.values, {}, spec)
    except Exception as exc:
        return {"variable": vid, "horizon": horizon, "error": str(exc), "seconds": round(time.monotonic() - t0, 1)}
    rows = [c for c in r["comparison"] if c.get("mase") is not None]
    auto = min(
        (c for c in rows if not c["experimental"]),
        key=lambda c: (c["mase"], list(MODELS).index(c["model"])),
    )
    return {
        "variable": vid,
        "label": v.label,
        "unit": sd.unit,
        "first": sd.first.isoformat() if sd.first else None,
        "last": sd.as_of.isoformat() if sd.as_of else None,
        "months": int(sd.values.notna().sum()),
        "missing": int(sd.values.isna().sum()),
        "horizon": horizon,
        "origins": r["backtestOrigins"],
        "auto": auto["model"],
        "autoSkill": auto.get("skill"),
        "autoMase": auto.get("mase"),
        "autoCoverage": auto.get("coverage"),
        "best": r["model"]["id"],
        "comparison": r["comparison"],
        "seconds": round(time.monotonic() - t0, 1),
    }


def fmt(v: Any, nd: int = 2) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


def render(results: list[dict[str, Any]], generated: str) -> str:
    models = list(MODELS)
    out = [
        "# Forecast validation on the NIWIS database",
        "",
        f"> Generated {generated} by `python scripts/forecast_validation.py` (read-only, Supabase). National series, "
        "built exactly as the API builds them; every model through the same rolling-origin backtest (4 origins, "
        "`min(h, 12)` months apart, each fitted only on data before its origin). **Nothing was tuned** for these "
        "numbers: the models run with the settings in `app/forecasting/` (DECISIONS D32–D34).",
        "",
        "**Skill** = 1 − squared error ÷ squared error of the same-month climatology on the same backtest points: "
        "> 0 beats the long-term average, ≤ 0 does not (the page then says so). **MASE** < 1 beats the seasonal naive "
        "forecast. **Auto** = the lowest MASE among non-experimental models — what the page forecasts use. "
        "Experimental models (LSTM, Transformer) are scored for comparison only.",
        "",
    ]
    for h in sorted({r["horizon"] for r in results}, reverse=True):
        rs = [r for r in results if r["horizon"] == h]
        out += [f"## Horizon {h} months", "", "### Auto's choice", ""]
        out += [
            "| Variable | Series | Auto picks | Skill vs climatology | MASE | Band coverage | Beats climatology | Run time (s) |",
            "| --- | --- | --- | ---: | ---: | ---: | --- | ---: |",
        ]
        for r in rs:
            if "error" in r:
                out.append(f"| {r['variable']} | — | **not forecast**: {r['error']} | — | — | — | — | {r['seconds']} |")
                continue
            beats = "yes" if (r["autoSkill"] or 0) > 0 else "**no**"
            gaps = f", {r['missing']} missing" if r["missing"] else ""
            out.append(
                f"| {r['label']} ({r['unit'] or '—'}) | {r['first'][:7]} – {r['last'][:7]}, {r['months']} months{gaps} "
                f"| {MODELS[r['auto']].label} | {fmt(r['autoSkill'])} | {fmt(r['autoMase'])} | "
                f"{fmt(r['autoCoverage'], 0)} % | {beats} | {r['seconds']} |"
            )
        out += ["", "### Skill against climatology, every model", ""]
        out += ["| Variable | " + " | ".join(MODELS[m].label for m in models) + " |"]
        out += ["| --- | " + " | ".join("---:" for _ in models) + " |"]
        for r in rs:
            if "error" in r:
                continue
            by = {c["model"]: c for c in r["comparison"]}
            cells = []
            for m in models:
                c = by.get(m)
                if c is None:
                    cells.append("n/a")
                elif c.get("error"):
                    cells.append("failed")
                else:
                    s = fmt(c.get("skill"))
                    cells.append(f"**{s}**" if m == r["auto"] else s)
            out.append(f"| {r['label']} | " + " | ".join(cells) + " |")
        out += ["", "### MASE, every model", ""]
        out += ["| Variable | " + " | ".join(MODELS[m].label for m in models) + " |"]
        out += ["| --- | " + " | ".join("---:" for _ in models) + " |"]
        for r in rs:
            if "error" in r:
                continue
            by = {c["model"]: c for c in r["comparison"]}
            cells = [fmt(by[m].get("mase")) if m in by and not by[m].get("error") else "n/a" for m in models]
            out.append(f"| {r['label']} | " + " | ".join(cells) + " |")
        out += ["", "### Fit time per model (s, all origins + no refit)", ""]
        out += ["| Variable | " + " | ".join(MODELS[m].label for m in models) + " |"]
        out += ["| --- | " + " | ".join("---:" for _ in models) + " |"]
        for r in rs:
            if "error" in r:
                continue
            by = {c["model"]: c for c in r["comparison"]}
            out.append(
                f"| {r['label']} | " + " | ".join(fmt(by[m]["seconds"], 1) if m in by else "n/a" for m in models) + " |"
            )
        out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--horizons", nargs="*", type=int, default=[24, 3])
    args = ap.parse_args()
    data = asyncio.run(series_for(set(args.only) if args.only else None))
    results = []
    for h in args.horizons:
        for vid, (v, t, sd) in data.items():
            print(f"h={h} {vid} …", flush=True)
            if sd.values.empty:
                results.append({"variable": vid, "horizon": h, "error": "no national series", "seconds": 0})
                continue
            r = one(vid, v, t, sd, h, "compare")
            results.append(r)
            print(
                f"   auto={r.get('auto')} skill={r.get('autoSkill')} {r['seconds']} s {r.get('error', '')}", flush=True
            )
    generated = dt.date.today().isoformat()
    if args.only and RAW.exists():  # a partial run replaces its variables' rows and keeps the others
        kept = [
            r for r in json.loads(RAW.read_text(encoding="utf-8"))["results"] if r["variable"] not in set(args.only)
        ]
        results = sorted(kept + results, key=lambda r: (-r["horizon"], list(VARS).index(r["variable"])))
    RAW.write_text(json.dumps({"generated": generated, "results": results}, indent=1), encoding="utf-8", newline="\n")
    DOC.write_text(render(results, generated), encoding="utf-8", newline="\n")
    print(f"wrote {DOC.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
