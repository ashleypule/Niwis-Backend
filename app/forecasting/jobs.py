"""Forecast runs in background worker processes (DECISIONS D35).

- Each run trains in its own spawned process; at most `FORECAST_WORKERS` (default 2) run at once, the rest wait in
  the queue. A process per run lets a run that passes `FORECAST_TIMEOUT_S` (default 120 s) be killed outright.
  Workers pin BLAS / OpenMP / torch to one thread each.
- Page forecasts (pages.py) use at most `FORECAST_WORKERS − 1` slots (at least one), so a Studio run never waits
  behind a whole round of page forecasts when two workers are configured.
- Results are cached by a hash of the request and the exact data it was trained on (target and input values, which
  carry the data's `asOf`): the same request on the same data never trains twice. A failed run is cached too, except
  timeouts and worker crashes, which may succeed when the server is less busy.
- Limits for a public API: `FORECAST_RUNS_PER_HOUR` new runs per client IP (default 10; cache hits are free), at most
  `FORECAST_QUEUE_LIMIT` runs queued or running (default 20), runs discarded `FORECAST_RETENTION_H` after they finish
  (default 24 h). Nothing is written to the database.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import multiprocessing as mp
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

from app.config import Settings
from app.core.errors import Busy, TooManyRequests
from app.forecasting import worker

log = logging.getLogger("niwis.forecast")
Status = Literal["queued", "running", "done", "failed"]
RETRYABLE = ("TIMEOUT", "WORKER_FAILED")


@dataclass
class RunRecord:
    id: str
    key: str
    request: dict[str, Any]
    context: dict[str, Any]  # target / scope / sources, added to the result
    background: bool
    status: Status = "queued"
    progress: float = 0.0
    message: str = "Queued"
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    task: asyncio.Task[None] | None = None

    def view(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "runId": self.id,
            "status": self.status,
            "progress": round(self.progress, 3),
            "message": self.message,
            "request": self.request,
            "createdAt": _iso(self.created),
        }
        if self.started:
            out["startedAt"] = _iso(self.started)
        if self.finished:
            out["finishedAt"] = _iso(self.finished)
        if self.status == "done" and self.result is not None:
            out["result"] = {**self.context, **self.result}
        if self.error:
            out["error"] = self.error
        return out


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def request_key(request: dict[str, Any], payload: dict[str, Any]) -> str:
    """Hash of the normalised request and the data it trains on (so new or revised data give a new key)."""
    blob = json.dumps(
        {"request": request, "target": payload["target"], "inputs": payload.get("inputs", {})},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


class ForecastJobs:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.workers = max(0, settings.forecast_workers)
        self.slots = asyncio.Semaphore(max(1, self.workers))
        self.bg_slots = asyncio.Semaphore(max(1, self.workers - 1))
        self.runs: dict[str, RunRecord] = {}
        self.started: deque[float] = deque()  # start times of Studio / on-demand runs in the last 30 days
        self.by_key: dict[str, str] = {}
        self.per_ip: dict[str, deque[float]] = {}
        worker.limit_threads()  # inherited by every spawned worker before it imports numpy
        self.mp = mp.get_context("spawn")

    # ---------------------------------------------------------------- bookkeeping

    def purge(self) -> None:
        keep = self.settings.forecast_retention_h * 3600
        now = time.time()
        for rid in [r.id for r in self.runs.values() if r.finished and now - r.finished > keep]:
            rec = self.runs.pop(rid)
            if self.by_key.get(rec.key) == rid:
                del self.by_key[rec.key]

    def pending(self) -> int:
        return sum(1 for r in self.runs.values() if r.status in ("queued", "running") and not r.background)

    def get(self, run_id: str) -> RunRecord | None:
        self.purge()
        return self.runs.get(run_id)

    def view(self, rec: RunRecord) -> dict[str, Any]:
        out = rec.view()
        if rec.finished:
            out["expiresAt"] = _iso(rec.finished + self.settings.forecast_retention_h * 3600)
        if rec.status == "queued":
            ahead = [r for r in self.runs.values() if r.status == "queued" and r.created < rec.created]
            out["queuePosition"] = len(ahead) + 1
        return out

    def check_ip(self, ip: str) -> None:
        limit = self.settings.forecast_runs_per_hour
        if limit <= 0:
            return
        q = self.per_ip.setdefault(ip, deque())
        now = time.monotonic()
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= limit:
            raise TooManyRequests(
                f"At most {limit} new forecast runs per hour from one address; results already computed are still "
                "served. Try again later.",
                retry_after_s=int(3600 - (now - q[0])) + 1,
            )
        q.append(now)
        if len(self.per_ip) > 10_000:
            for k in [k for k, v in self.per_ip.items() if not v or now - v[-1] > 3600]:
                del self.per_ip[k]

    # ---------------------------------------------------------------- submit

    def submit(
        self,
        request: dict[str, Any],
        payload: dict[str, Any],
        context: dict[str, Any],
        *,
        ip: str | None = None,
        background: bool = False,
    ) -> tuple[RunRecord, bool]:
        """(run, cached). A cached run is returned as it is (queued, running or finished)."""
        self.purge()
        key = request_key(request, payload)
        existing = self.runs.get(self.by_key.get(key, ""))
        if existing is not None and not (existing.error and existing.error.get("code") in RETRYABLE):
            return existing, True
        if not background:
            if self.pending() >= self.settings.forecast_queue_limit:
                raise Busy(
                    f"The forecast queue is full ({self.settings.forecast_queue_limit} runs); try again in a minute.",
                    retry_after_s=60,
                )
            if ip is not None:  # page forecasts made on demand carry no address and are not rate-limited
                self.check_ip(ip)
        rec = RunRecord(id=uuid.uuid4().hex[:16], key=key, request=request, context=context, background=background)
        self.runs[rec.id] = rec
        if not background:  # the AI page's "runs in 30 days" KPI (runs are not stored: counted since the API started)
            self.started.append(time.time())
            while self.started and time.time() - self.started[0] > 30 * 86400:
                self.started.popleft()
        self.by_key[key] = rec.id
        rec.task = asyncio.get_running_loop().create_task(self._run(rec, payload))
        return rec, False

    async def wait(self, rec: RunRecord) -> RunRecord:
        if rec.task is not None:
            await asyncio.shield(rec.task)
        return rec

    # ---------------------------------------------------------------- run

    async def _run(self, rec: RunRecord, payload: dict[str, Any]) -> None:
        try:
            if rec.background:
                async with self.bg_slots, self.slots:
                    await self._execute(rec, payload)
            else:
                async with self.slots:
                    await self._execute(rec, payload)
        except asyncio.CancelledError:
            rec.status, rec.error, rec.finished = (
                "failed",
                {"code": "CANCELLED", "detail": "Server shutting down"},
                time.time(),
            )
            raise
        except Exception:
            log.exception("forecast run failed", extra={"run": rec.id})
            rec.status, rec.finished = "failed", time.time()
            rec.error = {"code": "WORKER_FAILED", "detail": "The forecast worker failed; try again."}

    async def _execute(self, rec: RunRecord, payload: dict[str, Any]) -> None:
        rec.status, rec.started, rec.message, rec.progress = "running", time.time(), "Starting", 0.02
        if self.workers == 0:  # in-process (tests, debugging): no hard timeout
            out = await asyncio.to_thread(worker.outcome, payload, lambda f, m: self._progress(rec, f, m))
            self._finish(rec, out)
            return
        parent, child = self.mp.Pipe(duplex=False)
        proc = self.mp.Process(target=worker.main, args=(payload, child), daemon=True)
        proc.start()
        child.close()
        deadline = time.monotonic() + self.settings.forecast_timeout_s
        msg: tuple[Any, ...] | None = None
        try:
            while msg is None:
                while parent.poll():
                    try:
                        m = parent.recv()
                    except EOFError:
                        break
                    if m[0] == "progress":
                        self._progress(rec, m[1], m[2])
                    else:
                        msg = m
                        break
                if msg is not None:
                    break
                if time.monotonic() > deadline:
                    msg = (
                        "error",
                        "TIMEOUT",
                        f"The run took longer than {self.settings.forecast_timeout_s} s and was "
                        "stopped. Choose a shorter horizon or a single model.",
                    )
                    break
                if not proc.is_alive() and not parent.poll():
                    msg = ("error", "WORKER_FAILED", "The forecast worker stopped unexpectedly; try again.")
                    break
                await asyncio.sleep(0.2)
        finally:
            if proc.is_alive():
                proc.kill()
            await asyncio.to_thread(proc.join, 5)
            parent.close()
        self._finish(rec, msg)

    def _progress(self, rec: RunRecord, fraction: float, message: str) -> None:
        rec.progress, rec.message = max(rec.progress, float(fraction)), message

    def _finish(self, rec: RunRecord, msg: tuple[Any, ...]) -> None:
        rec.finished = time.time()
        if msg[0] == "result":
            rec.status, rec.result, rec.progress, rec.message = "done", msg[1], 1.0, "Done"
        else:
            rec.status, rec.error, rec.message = "failed", {"code": str(msg[1]), "detail": str(msg[2])}, str(msg[2])
        log.info(
            "forecast run finished",
            extra={
                "run": rec.id,
                "status": rec.status,
                "seconds": round(rec.finished - (rec.started or rec.created), 1),
            },
        )

    async def close(self) -> None:
        for rec in self.runs.values():
            if rec.task is not None and not rec.task.done():
                rec.task.cancel()
