"""Entry point of a forecast worker process. Kept import-light: the engine (numpy, statsmodels, torch) is imported only
after the BLAS / OpenMP / torch thread counts are pinned to 1, so two workers never fight over every CPU."""

from __future__ import annotations

import os
from multiprocessing.connection import Connection
from typing import Any

THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")


def limit_threads() -> None:
    for var in THREAD_VARS:
        os.environ[var] = "1"


def payload_series(raw: dict[str, Any]) -> Any:
    import pandas as pd

    return pd.Series(raw["values"], index=pd.DatetimeIndex(raw["index"]), dtype=float)


def execute(payload: dict[str, Any], progress: Any = None) -> dict[str, Any]:
    """Run one forecast from a plain-data payload (also used in-process when `FORECAST_WORKERS=0`)."""
    from app.forecasting.engine import RunSpec, run

    y = payload_series(payload["target"])
    inputs = {k: payload_series(v) for k, v in payload.get("inputs", {}).items()}
    spec = RunSpec(**payload["spec"])
    return run(y, inputs, spec, progress)


def outcome(payload: dict[str, Any], progress: Any = None) -> tuple[Any, ...]:
    """("result", dict) or ("error", code, detail): a series the data cannot support is UNPROCESSABLE (422-style)."""
    from app.forecasting.validation import SeriesError

    try:
        return ("result", execute(payload, progress))
    except SeriesError as exc:
        return ("error", "UNPROCESSABLE", str(exc))
    except Exception as exc:  # reported as a failed run; the trace goes to this process's stderr / the log
        import traceback

        traceback.print_exc()
        return ("error", "MODEL_FAILED", f"The forecast failed: {type(exc).__name__}")


def main(payload: dict[str, Any], conn: Connection) -> None:
    """Child process: sends ("progress", fraction, message)…, then the outcome."""
    limit_threads()
    try:
        import torch

        torch.set_num_threads(1)
    except Exception:  # noqa: S110 — torch is optional for every model but the deep ones
        pass

    def progress(fraction: float, message: str) -> None:
        conn.send(("progress", round(fraction, 3), message))

    try:
        conn.send(outcome(payload, progress))
    finally:
        conn.close()
