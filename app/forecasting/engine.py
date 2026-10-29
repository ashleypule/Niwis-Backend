"""One forecast run, start to finish (pure computation: no database, no I/O; runs in a worker process).

1. Check the series: at least 36 months (daily: 730 days) after trimming, not constant, at most 20 % of periods
   missing; inputs must reach the target's last period.
2. Rolling-origin backtest of every candidate model (validation.py), with inputs forecast at each origin from the data
   before it.
3. Pick the model: the requested one, or (Auto / Compare all) the lowest backtest MASE.
4. Refit the chosen model on all data and forecast the horizon; interval from the model or from its backtest errors.
5. Physical bounds (never below 0 for amounts, never above 100 for % shares) and, for SPI / SPEI, the D0–D4 class
   of the median and the probability of each class.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.core.thresholds import drought_class, thresholds
from app.forecasting.base import PANDAS_FREQ, Z90, ForecastModel, Freq, ModelError, future_index
from app.forecasting.registry import AUTO, BASELINES, COMPARE, MODELS, allowed, candidates
from app.forecasting.statistical import ETS, SARIMA
from app.forecasting.validation import (
    Backtest,
    OriginResult,
    SeriesError,
    bands_for,
    fill_gaps,
    metrics,
    naive_scale,
    plan_origins,
    residual_band,
)

MIN_HISTORY: dict[Freq, int] = {"monthly": 36, "daily": 730}
MAX_HORIZON: dict[Freq, int] = {"monthly": 24, "daily": 30}
MAX_MISSING = 0.20  # share of periods missing (after trimming) above which a series is refused
WARN_MISSING = 0.10  # spec §22.2: < 10 % missing after gap filling for training data
DAILY_HISTORY_SHOWN = 730  # daily results return the last two years of history (the chart's window)
Progress = Callable[[float, str], None]


@dataclass
class RunSpec:
    freq: Freq
    horizon: int
    model: str  # "auto", "compare" or a model id
    classify: bool = False  # SPI / SPEI target: report D0–D4 classes and their probabilities
    clip_low: float | None = None
    clip_high: float | None = None
    seed: int = 0
    labels: dict[str, str] = field(default_factory=dict)  # input column → label (warnings)


def _regular(y: pd.Series, freq: Freq) -> pd.Series:
    y = y.sort_index().astype(float)
    if y.empty:
        return y
    idx = pd.date_range(y.index.min(), y.index.max(), freq=PANDAS_FREQ[freq])
    return y.reindex(idx)


def check_target(y: pd.Series, spec: RunSpec, warnings: list[str]) -> pd.Series:
    if spec.horizon < 1 or spec.horizon > MAX_HORIZON[spec.freq]:
        unit = "months" if spec.freq == "monthly" else "days"
        raise SeriesError(f"The horizon must be 1–{MAX_HORIZON[spec.freq]} {unit} for {spec.freq} forecasts")
    y = _regular(y, spec.freq)
    first, last = y.first_valid_index(), y.last_valid_index()
    if first is None or last is None:
        raise SeriesError("The series holds no values for this area")
    y = y.loc[first:last]
    n, unit = len(y), ("months" if spec.freq == "monthly" else "days")
    if n < MIN_HISTORY[spec.freq]:
        raise SeriesError(
            f"At least {MIN_HISTORY[spec.freq]} {unit} of history are needed; this series has {n} "
            f"({first:%b %Y} – {last:%b %Y})"
        )
    v = y.dropna()
    if v.nunique() <= 1 or float(v.std()) < 1e-12 * max(1.0, abs(float(v.mean()))):
        raise SeriesError(
            f"The series is constant ({float(v.iloc[0]):g} in every period): there is nothing to forecast"
        )
    missing = float(y.isna().mean())
    if missing > MAX_MISSING:
        raise SeriesError(
            f"{missing:.0%} of the {n} {unit} between {first:%b %Y} and {last:%b %Y} have no value (at most "
            f"{MAX_MISSING:.0%} can be filled)"
        )
    if missing > WARN_MISSING:
        warnings.append(
            f"{missing:.0%} of the periods have no value; gaps were filled inside each training window from that "
            "window only (same-month mean plus the interpolated anomaly)."
        )
    elif missing > 0:
        warnings.append(f"{int(y.isna().sum())} missing period(s) were filled inside each training window.")
    return y


def check_inputs(y: pd.Series, inputs: dict[str, pd.Series], spec: RunSpec) -> dict[str, pd.Series]:
    out = {}
    for name, x in inputs.items():
        label = spec.labels.get(name, name)
        x = _regular(x, spec.freq)
        last = x.last_valid_index()
        if last is None:
            raise SeriesError(f"Input {label} holds no values for this area")
        if last < y.index[-1]:
            raise SeriesError(
                f"Input {label} ends {last:%b %Y}, before the target's last month ({y.index[-1]:%b %Y}); a forecast "
                "with it would start in the past. Choose inputs that are as current as the target."
            )
        x = x.reindex(y.index)
        if x.isna().mean() > MAX_MISSING:
            raise SeriesError(f"Input {label} has no value in {x.isna().mean():.0%} of the target's months")
        if x.dropna().nunique() <= 1:
            raise SeriesError(f"Input {label} is constant over the target's months")
        out[name] = x
    return out


def _window(y: pd.Series, end: int, freq: Freq) -> pd.Series:
    """The training window before index `end`, gaps filled from the window only."""
    return fill_gaps(y.iloc[:end], freq)


def forecast_input(x: pd.Series, steps: int, freq: Freq, seed: int) -> tuple[np.ndarray, str]:
    """An input's own forecast for `steps` periods from its best statistical model (ETS or SARIMA, whichever has the
    lower MAE on the window's last min(12, steps) periods, then refitted on the whole window)."""
    k = min(12, steps)
    best: tuple[float, type[ForecastModel]] | None = None
    for m in (ETS, SARIMA):
        try:
            p = m(freq, seed).fit(x.iloc[:-k]).predict(k).mean
        except Exception:  # noqa: S112 — the other candidate is used
            continue
        mae = float(np.mean(np.abs(p - x.iloc[-k:].to_numpy())))
        if best is None or mae < best[0]:
            best = (mae, m)
    if best is None:
        raise ModelError("no statistical model could forecast an input")
    return best[1](freq, seed).fit(x).predict(steps).mean, best[1].id


class Run:
    def __init__(self, y: pd.Series, inputs: dict[str, pd.Series], spec: RunSpec, progress: Progress | None) -> None:
        self.spec, self.freq, self.h = spec, spec.freq, spec.horizon
        self.warnings: list[str] = []
        self.progress = progress or (lambda f, m: None)
        self.y = check_target(y, spec, self.warnings)
        self.inputs = check_inputs(self.y, inputs, spec) if inputs else {}
        if self.inputs and self.freq == "daily":
            raise SeriesError("Input variables are supported for monthly forecasts only")
        self.signed = bool(self.y.min() <= 0)
        self._input_fc: dict[int, pd.DataFrame] = {}
        self.input_models: dict[str, str] = {}

    # ---------------------------------------------------------------- inputs

    def exog_train(self, end: int) -> pd.DataFrame | None:
        if not self.inputs:
            return None
        return pd.DataFrame({k: _window(x, end, self.freq) for k, x in self.inputs.items()})

    def exog_future(self, end: int, index: pd.DatetimeIndex) -> pd.DataFrame | None:
        """The inputs' forecasts made at `end` (one set per origin, shared by every model)."""
        if not self.inputs:
            return None
        if end not in self._input_fc:
            cols = {}
            for k, x in self.inputs.items():
                cols[k], used = forecast_input(_window(x, end, self.freq), self.h, self.freq, self.spec.seed)
                self.input_models[k] = used
            self._input_fc[end] = pd.DataFrame(cols, index=index)
        return self._input_fc[end].set_axis(index)

    # ---------------------------------------------------------------- one model at one origin

    def fit_predict(self, model_id: str, end: int, steps: int) -> tuple[ForecastModel, Any]:
        model = MODELS[model_id](self.freq, self.spec.seed)
        train = _window(self.y, end, self.freq)
        use_x = model.supports_exog and bool(self.inputs)
        ex = self.exog_train(end) if use_x else None
        if ex is not None:
            ex = ex.reindex(train.index)
            if ex.isna().any().any():  # an input starts later than the target window: train on the common span
                start = ex.dropna().index[0]
                train, ex = train.loc[start:], ex.loc[start:]
        model.fit(train, ex)
        fut = self.exog_future(end, model.future_index(steps)) if use_x else None
        return model, model.predict(steps, fut)

    # ---------------------------------------------------------------- run

    def models(self) -> tuple[list[str], list[int], dict[str, str]]:
        n, has_x = len(self.y), bool(self.inputs)
        choice = self.spec.model
        if choice not in (AUTO, COMPARE):
            why = allowed(MODELS[choice], self.freq, n, has_x)
            if why:
                raise SeriesError(why)
        ids = candidates(choice, self.freq, n, has_x)
        skipped: dict[str, str] = {}
        base_min = MIN_HISTORY[self.freq]
        need = max([base_min, *(MODELS[m].minimum(self.freq) for m in ids)])
        try:
            origins = plan_origins(n, self.h, need, self.freq)
        except SeriesError:
            if need == base_min:
                raise
            origins = plan_origins(n, self.h, base_min, self.freq)
        keep = []
        for m in ids:
            if MODELS[m].minimum(self.freq) > origins[0]:
                skipped[m] = f"needs {MODELS[m].minimum(self.freq)} periods before the first backtest origin"
            else:
                keep.append(m)
        if choice not in (AUTO, COMPARE) and choice in skipped:
            raise SeriesError(f"{MODELS[choice].label} {skipped[choice]}: choose a shorter horizon")
        return keep, origins, skipped

    def execute(self) -> dict[str, Any]:
        t0 = time.monotonic()
        ids, origins, skipped = self.models()
        backtests: dict[str, Backtest] = {m: Backtest(m, native=MODELS[m].native_interval) for m in ids}
        failed: dict[str, str] = dict(skipped)
        timing: dict[str, float] = dict.fromkeys(ids, 0.0)
        steps_total, done = len(ids) * (len(origins) + 1), 0
        for o in origins:
            actual = self.y.iloc[o : o + self.h].to_numpy()
            index = pd.DatetimeIndex(self.y.index[o : o + self.h])
            scale = naive_scale(_window(self.y, o, self.freq).to_numpy(), self.freq)
            for m in ids:
                done += 1
                if m in failed:
                    continue
                self.progress(0.05 + 0.8 * done / steps_total, f"Backtest {MODELS[m].label}, origin {index[0]:%b %Y}")
                t = time.monotonic()
                try:
                    _, p = self.fit_predict(m, o, len(actual))
                except Exception as exc:
                    failed[m] = _why(exc)
                    continue
                finally:
                    timing[m] += time.monotonic() - t
                pred = self.clip(p.mean)
                lo = self.clip(p.lower) if p.lower is not None else None
                hi = self.clip(p.upper) if p.upper is not None else None
                backtests[m].origins.append(OriginResult(index[0], index, actual, pred, lo, hi, scale))
        ok = {m: bt for m, bt in backtests.items() if m not in failed}
        if not ok:
            raise SeriesError(
                "No model could be fitted on this series: " + "; ".join(f"{k}: {v}" for k, v in failed.items())
            )
        clim = ok.get("climatology")
        scores = {m: metrics(bt, clim, self.h, self.signed) for m, bt in ok.items()}
        choice = self.spec.model
        if choice in (AUTO, COMPARE):
            winner = min(ok, key=lambda m: (_num(scores[m].get("mase")), list(MODELS).index(m)))
        elif choice in ok:
            winner = choice
        else:
            raise SeriesError(f"{MODELS[choice].label} could not be fitted: {failed.get(choice, 'unknown error')}")
        self.progress(0.88, f"Refitting {MODELS[winner].label} on all data")
        model, p = self.fit_predict(winner, len(self.y), self.h)
        if p.lower is not None and p.upper is not None:
            lower, upper, interval = p.lower, p.upper, "model"
        else:
            lo_off, hi_off = residual_band(ok[winner].errors(), self.h)
            lower, upper, interval = p.mean + lo_off, p.mean + hi_off, "backtest residuals"
        mean = self.clip(p.mean)
        lower, upper = self.clip(np.minimum(lower, mean)), self.clip(np.maximum(upper, mean))
        fidx = future_index(pd.Timestamp(self.y.index[-1]), self.h, self.freq)
        result = self.result(ok, scores, failed, winner, model, fidx, mean, lower, upper, interval, origins, timing)
        result["runtimeSeconds"] = round(time.monotonic() - t0, 2)
        self.progress(1.0, "Done")
        return result

    def clip(self, v: np.ndarray) -> np.ndarray:
        if self.spec.clip_low is not None:
            v = np.maximum(v, self.spec.clip_low)
        if self.spec.clip_high is not None:
            v = np.minimum(v, self.spec.clip_high)
        return v

    # ---------------------------------------------------------------- result

    def why(self, winner: str, scores: dict[str, dict[str, Any]], choice: str) -> str:
        w = MODELS[winner].label
        mase = scores[winner].get("mase")
        if choice not in (AUTO, COMPARE):
            return f"{w} was requested."
        others = sorted(
            ((m, s.get("mase")) for m, s in scores.items() if m != winner and s.get("mase") is not None),
            key=lambda x: x[1],  # type: ignore[arg-type, return-value]
        )
        runner = f"; next best {MODELS[others[0][0]].label} ({others[0][1]:.2f})" if others else ""
        base = min((s.get("mase") or math.inf for m, s in scores.items() if m in BASELINES), default=math.inf)
        verdict = (
            "It does not beat the best simple baseline."
            if winner in BASELINES
            else f"Best baseline MASE {base:.2f}."
            if math.isfinite(base)
            else ""
        )
        return (f"{w} had the lowest backtest MASE ({_fmt(mase)}) of {len(scores)} models{runner}. {verdict}").strip()

    def result(
        self,
        ok: dict[str, Backtest],
        scores: dict[str, dict[str, Any]],
        failed: dict[str, str],
        winner: str,
        model: ForecastModel,
        fidx: pd.DatetimeIndex,
        mean: np.ndarray,
        lower: np.ndarray,
        upper: np.ndarray,
        interval: str,
        origins: list[int],
        timing: dict[str, float],
    ) -> dict[str, Any]:
        spec, w = self.spec, scores[winner]
        hist = self.y if self.freq == "monthly" else self.y.iloc[-DAILY_HISTORY_SHOWN:]
        bands = bands_for(ok[winner], self.h)
        backtest = [
            {
                "origin": _d(o.origin),
                "data": [
                    [_d(t), _r(a), _r(p), _r(lo), _r(hi)]
                    for t, a, p, lo, hi in zip(o.index, o.actual, o.pred, b[0], b[1], strict=True)
                ],
            }
            for o, b in zip(ok[winner].origins, bands, strict=True)
        ]
        comparison = []
        for m in MODELS:
            if m not in scores and m not in failed:
                continue
            s = scores.get(m, {})
            comparison.append(
                {
                    "model": m,
                    "label": MODELS[m].label,
                    "group": MODELS[m].group,
                    "baseline": m in BASELINES,
                    "experimental": MODELS[m].experimental,
                    "winner": m == winner,
                    **({k: _r(v) for k, v in s.items() if k != "n"}),
                    "seconds": round(timing.get(m, 0.0), 2),
                    **({"error": failed[m]} if m in failed else {}),
                }
            )
        warnings = list(self.warnings)
        skill = w.get("skill")
        if skill is not None and skill <= 0:
            warnings.append(
                "This model does not beat the long-term average for this variable (skill against climatology "
                f"{skill:.2f}): treat the forecast as no better than the same-month mean."
            )
        if self.inputs:
            used = ", ".join(f"{spec.labels.get(k, k)} ({v.upper()})" for k, v in self.input_models.items())
            warnings.append(
                "Future values of the input variables are their own forecasts (best statistical model per input: "
                f"{used}), not observed values; errors in them carry into this forecast. In the backtest the inputs "
                "were forecast from each origin too."
            )
        if MODELS[winner].experimental:
            warnings.append(f"{MODELS[winner].label} is experimental: compare its skill with the baselines.")
        if interval == "backtest residuals":
            warnings.append(
                f"{MODELS[winner].label} has no interval of its own: the 10–90 % band comes from its backtest errors "
                f"({len(ok[winner].origins)} origins), so it is approximate."
            )
        cov = w.get("coverage")
        if cov is not None and (cov < 60 or cov > 95):
            warnings.append(
                f"In the backtest {cov:.0f} % of the actual values fell inside the 10–90 % band (80 % expected): the "
                "band is " + ("too narrow." if cov < 60 else "wider than needed.")
            )
        out: dict[str, Any] = {
            "model": {
                "id": winner,
                "label": MODELS[winner].label,
                "group": MODELS[winner].group,
                "experimental": MODELS[winner].experimental,
                "params": _jsonable(model.params()),
                "why": self.why(winner, scores, spec.model),
                "interval": interval,
            },
            "request": {"model": spec.model, "horizon": self.h, "resolution": self.freq},
            "history": [[_d(t), _r(v)] for t, v in hist.items()],
            "historyStart": _d(self.y.index[0]),
            "forecastStart": _d(fidx[0]),
            "asOf": _d(self.y.index[-1]),
            "backtest": backtest,
            "backtestOrigins": [_d(self.y.index[o]) for o in origins],
            "forecast": [[_d(t), _r(m), _r(lo), _r(hi)] for t, m, lo, hi in zip(fidx, mean, lower, upper, strict=True)],
            "metrics": {k: _r(v) for k, v in w.items()},
            "metricNotes": {
                "mase": "mean absolute error ÷ in-sample error of the seasonal naive forecast (< 1 beats it)",
                "skill": "1 − squared error ÷ that of the same-month climatology (> 0 beats the long-term average)",
                "coverage": "share of backtest actuals inside the 10–90 % band (80 % expected)",
                "smape" if self.signed else "mape": "symmetric MAPE (the series reaches zero or changes sign)"
                if self.signed
                else "mean absolute percentage error",
            },
            "comparison": comparison,
            "warnings": warnings,
        }
        if spec.classify:
            out["classes"] = class_probabilities(fidx, mean, lower, upper)
        return out


def class_probabilities(
    index: pd.DatetimeIndex, mean: np.ndarray, lower: np.ndarray, upper: np.ndarray
) -> list[dict[str, Any]]:
    """D0–D4 class of the median and each class's probability, from a normal distribution with the median as its
    centre and the 10–90 % band as ±1.2816 σ (spec §3.4 thresholds, applied as published: ≤ the class maximum)."""
    from scipy.stats import norm

    cfg = thresholds()["drought"]
    edges = [(str(c["id"]), float(c["max"])) for c in cfg["classes"]]  # D4 (−2.0) … D0 (−0.5), ascending maxima
    out = []
    for t, m, lo, hi in zip(index, mean, lower, upper, strict=True):
        sd = max((hi - lo) / (2 * Z90), 1e-6)
        probs: dict[str, float] = {}
        prev = 0.0
        for cid, mx in edges:
            cdf = float(norm.cdf(mx + 0.005, loc=m, scale=sd))  # values round to 2 dp, then compare with ≤
            probs[cid] = max(0.0, cdf - prev)
            prev = cdf
        probs["None"] = max(0.0, 1.0 - prev)
        out.append(
            {
                "date": _d(t),
                "class": drought_class(float(m)),
                "probabilities": {k: round(v * 100, 1) for k, v in probs.items()},
                "pDrought": round(sum(probs[k] for k in ("D1", "D2", "D3", "D4")) * 100, 1),
            }
        )
    return out


def run(y: pd.Series, inputs: dict[str, pd.Series], spec: RunSpec, progress: Progress | None = None) -> dict[str, Any]:
    return Run(y, inputs, spec, progress).execute()


# ---------------------------------------------------------------- helpers


def _why(exc: Exception) -> str:
    msg = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return msg[:200]


def _num(v: Any) -> float:
    return float(v) if v is not None and np.isfinite(v) else math.inf


def _fmt(v: Any) -> str:
    return f"{v:.2f}" if v is not None and np.isfinite(v) else "n/a"


def _d(t: Any) -> str:
    return str(pd.Timestamp(t).date().isoformat())


def _r(v: Any, nd: int = 4) -> float | None:
    if v is None:
        return None
    f = float(v)
    if not math.isfinite(f):
        return None
    return round(f, nd)


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def precheck(y: pd.Series, inputs: dict[str, pd.Series], spec: RunSpec) -> dict[str, Any]:
    """Every check a run makes before fitting anything (cheap: no model is fitted), so the API can answer 422 at once
    instead of after queueing. Returns the models the run will fit and the backtest origins."""
    r = Run(y, inputs, spec, None)
    ids, origins, skipped = r.models()
    return {
        "models": ids,
        "skipped": skipped,
        "origins": [_d(r.y.index[o]) for o in origins],
        "history": len(r.y),
        "warnings": r.warnings,
    }
