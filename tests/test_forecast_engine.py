"""Forecasting engine on synthetic series with known answers (no database): every model, validation, leakage, limits,
intervals and drought classes."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.forecasting.base import ForecastModel
from app.forecasting.engine import RunSpec, class_probabilities, precheck, run
from app.forecasting.registry import BASELINES, MODELS, allowed, candidates, catalogue
from app.forecasting.validation import SeriesError, fill_gaps, plan_origins, residual_band


def seasonal(n: int = 300, trend: float = 0.02, noise: float = 0.5, seed: int = 1) -> pd.Series:
    """A seasonal sine plus a linear trend plus noise, monthly from Jan 2000."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    v = 10 + trend * t + 5 * np.sin(2 * np.pi * t / 12) + rng.normal(0, noise, n)
    return pd.Series(v, index=pd.date_range("2000-01-01", periods=n, freq="MS"))


def mase(result: dict, model: str) -> float:
    return next(r["mase"] for r in result["comparison"] if r["model"] == model)


# ---------------------------------------------------------------- registry


def test_registry_lists_every_required_model_and_no_garch() -> None:
    ids = {m["id"] for m in catalogue()}
    assert {"naive", "seasonal-naive", "climatology", "moving-average"} <= ids
    assert {"ets", "sarima", "sarimax", "prophet", "random-forest", "lightgbm", "lstm", "transformer", "var"} <= ids
    assert not any("garch" in i for i in ids)
    for m in catalogue():
        assert m["description"] and m["group"] in ("baseline", "statistical", "ml", "deep", "multivariate")
    assert all(MODELS[k].experimental for k in ("lstm", "transformer"))
    assert MODELS["lstm"].min_obs == 60 and MODELS["ets"].min_obs == 36


def test_candidates_auto_excludes_experimental_and_input_models_without_inputs() -> None:
    auto = candidates("auto", "monthly", 300, False)
    assert "lstm" not in auto and "sarimax" not in auto and "var" not in auto
    assert set(BASELINES) <= set(auto)
    assert {"lstm", "transformer"} <= set(candidates("compare", "monthly", 300, False))
    assert {"sarimax", "var"} <= set(candidates("auto", "monthly", 300, True))
    assert not {"lstm", "transformer"} & set(candidates("compare", "monthly", 300, True))  # single-target only
    assert set(candidates("auto", "daily", 2000, False)) == {*BASELINES, "ets", "sarima"}
    assert set(candidates("auto-fast", "monthly", 300, False)) == {*BASELINES, "ets", "lightgbm"}
    assert allowed(MODELS["lstm"], "monthly", 50, False)  # needs 60 months
    assert allowed(MODELS["prophet"], "daily", 5000, False)  # monthly only


# ---------------------------------------------------------------- every model fits and forecasts


@pytest.mark.parametrize("model_id", [k for k in MODELS if not MODELS[k].requires_exog])
def test_every_univariate_model_forecasts_the_horizon(model_id: str) -> None:
    y = seasonal(120)
    m: ForecastModel = MODELS[model_id]("monthly", 0).fit(y)
    p = m.predict(6)
    assert p.mean.shape == (6,) and np.all(np.isfinite(p.mean))
    if MODELS[model_id].native_interval:
        assert p.lower is not None and p.upper is not None and np.all(p.lower <= p.upper)


@pytest.mark.parametrize("model_id", ["sarimax", "var", "random-forest", "lightgbm"])
def test_models_with_inputs_need_future_inputs(model_id: str) -> None:
    y, x = seasonal(120), seasonal(120, seed=2).rename("x")
    m = MODELS[model_id]("monthly", 0).fit(y, x.to_frame())
    fut = pd.DataFrame({"x": np.linspace(10, 12, 6)}, index=m.future_index(6))
    assert np.all(np.isfinite(m.predict(6, fut).mean))


# ---------------------------------------------------------------- known answers


def test_ets_and_sarima_beat_naive_on_a_seasonal_series_with_trend() -> None:
    for model in ("ets", "sarima"):
        r = run(seasonal(), {}, RunSpec("monthly", 12, model))
        assert r["model"]["id"] == model
        assert mase(r, model) < mase(r, "naive")
        assert mase(r, model) < 1.0  # also beats the seasonal naive forecast
        assert r["metrics"]["skill"] > 0.5  # far better than climatology (the series has a trend)
        assert len(r["forecast"]) == 12 and len(r["backtest"]) >= 3


def test_auto_picks_the_lowest_backtest_mase_and_says_why() -> None:
    r = run(seasonal(200), {}, RunSpec("monthly", 6, "auto"))
    scores = {c["model"]: c["mase"] for c in r["comparison"] if c.get("mase") is not None}
    assert r["model"]["id"] == min(scores, key=lambda k: scores[k])
    assert "lowest backtest MASE" in r["model"]["why"]
    assert {c["model"] for c in r["comparison"]} >= set(BASELINES)  # baselines always in the table
    assert sum(c["winner"] for c in r["comparison"]) == 1


def test_constant_series_is_rejected() -> None:
    y = pd.Series(4.2, index=pd.date_range("2000-01-01", periods=120, freq="MS"))
    with pytest.raises(SeriesError, match="constant"):
        run(y, {}, RunSpec("monthly", 6, "auto"))


def test_short_series_is_rejected_with_the_reason() -> None:
    with pytest.raises(SeriesError, match="At least 36 months"):
        precheck(seasonal(30), {}, RunSpec("monthly", 6, "auto"))
    with pytest.raises(SeriesError, match="needs at least 60 months"):
        precheck(seasonal(50), {}, RunSpec("monthly", 6, "lstm"))


def test_horizon_limits() -> None:
    with pytest.raises(SeriesError, match="1–24 months"):
        precheck(seasonal(), {}, RunSpec("monthly", 25, "auto"))
    days = pd.Series(np.sin(np.arange(1500) / 58.0) + 5, index=pd.date_range("2020-01-01", periods=1500, freq="D"))
    with pytest.raises(SeriesError, match="1–30 days"):
        precheck(days, {}, RunSpec("daily", 31, "auto"))
    with pytest.raises(SeriesError, match="monthly series only"):
        precheck(days, {}, RunSpec("daily", 7, "prophet"))


def test_too_many_gaps_are_refused_and_few_are_filled() -> None:
    y = seasonal(120)
    holes = y.copy()
    holes.iloc[10:40] = np.nan  # 25 %
    with pytest.raises(SeriesError, match="have no value"):
        precheck(holes, {}, RunSpec("monthly", 6, "auto"))
    some = y.copy()
    some.iloc[[20, 50, 51]] = np.nan
    r = run(some, {}, RunSpec("monthly", 6, "ets"))
    assert any("filled inside each training window" in w for w in r["warnings"])


# ---------------------------------------------------------------- validation


def test_backtest_never_sees_data_after_its_origin() -> None:
    """Leakage test: change every value after the first origin's forecast window; that origin's predictions must not
    change (for a model with an AIC search, a learned model and a model with inputs)."""
    y = seasonal(110)
    h = 6
    for model, inputs in (("sarima", False), ("random-forest", False), ("sarimax", True)):
        x = {"x": seasonal(110, seed=7)} if inputs else {}
        a = run(y, x, RunSpec("monthly", h, model))
        first = pd.Timestamp(a["backtestOrigins"][0])
        y2 = y.copy()
        y2[y2.index >= first] = y2[y2.index >= first] * 3 + 100  # future values, wildly different
        x2 = {"x": x["x"].where(x["x"].index < first, -50.0)} if inputs else {}
        b = run(y2, x2, RunSpec("monthly", h, model))
        assert b["backtestOrigins"][0] == a["backtestOrigins"][0]
        pa = [p[2] for p in a["backtest"][0]["data"]]
        pb = [p[2] for p in b["backtest"][0]["data"]]
        assert pa == pb, model
        assert a["backtest"][1]["data"] != b["backtest"][1]["data"]  # a later origin does see the change


def test_gap_filling_uses_the_window_only() -> None:
    y = seasonal(60)
    y.iloc[30] = np.nan
    window = fill_gaps(y.iloc[:40], "monthly")
    later_changed = y.copy()
    later_changed.iloc[40:] += 1000
    assert fill_gaps(later_changed.iloc[:40], "monthly").equals(window)
    assert np.isfinite(window.iloc[30])


def test_origins_are_chronological_and_leave_training_data() -> None:
    o = plan_origins(300, 12, 36, "monthly")
    assert o == sorted(o) and len(o) == 4 and o[-1] + 12 == 300 and o[0] >= 36
    with pytest.raises(SeriesError, match="Choose a shorter horizon"):
        plan_origins(60, 24, 36, "monthly")


def test_residual_band_widens_with_the_horizon() -> None:
    rng = np.random.default_rng(0)
    errors = rng.normal(0, 1, (4, 12)) * np.arange(1, 13)
    lo, hi = residual_band(errors, 12)
    assert np.all(np.diff(hi) >= 0) and np.all(np.diff(lo) <= 0)
    assert np.all(lo <= 0) or np.all(hi >= 0)


def test_metrics_and_interval_coverage_are_reported() -> None:
    r = run(seasonal(), {}, RunSpec("monthly", 12, "random-forest"))
    m = r["metrics"]
    assert {"mae", "rmse", "mase", "skill", "coverage", "mape"} <= set(m)
    assert 0 <= m["coverage"] <= 100
    assert r["model"]["interval"] == "backtest residuals"
    assert all(lo <= med <= hi for _, med, lo, hi in r["forecast"])


def test_smape_for_series_through_zero() -> None:
    spi = seasonal(150, trend=0.0) - 10  # crosses zero
    r = run(spi, {}, RunSpec("monthly", 6, "ets"))
    assert "smape" in r["metrics"] and "mape" not in r["metrics"]


def test_physical_bounds_clip_the_forecast() -> None:
    rain = (seasonal(150, trend=-0.06) - 4).clip(lower=0)
    r = run(rain, {}, RunSpec("monthly", 24, "naive", clip_low=0.0, clip_high=None))
    assert all(lo >= 0 for _, _, lo, _ in r["forecast"])


def test_inputs_use_their_own_forecasts_and_say_so() -> None:
    x = seasonal(200, seed=3)
    y = 0.5 * x.shift(1).bfill() + seasonal(200, trend=0, seed=4)
    r = run(y, {"t.x": x}, RunSpec("monthly", 6, "sarimax", labels={"t.x": "X"}))
    assert any("their own forecasts" in w for w in r["warnings"])
    assert r["model"]["params"]["inputs"] == ["t.x"]


def test_inputs_must_reach_the_targets_last_month() -> None:
    y, x = seasonal(200), seasonal(190, seed=5)
    with pytest.raises(SeriesError, match="before the target's last month"):
        precheck(y, {"t.x": x}, RunSpec("monthly", 6, "var", labels={"t.x": "X"}))


def test_daily_runs_use_baselines_ets_and_sarima() -> None:
    idx = pd.date_range("2018-01-01", "2023-12-31", freq="D")
    rng = np.random.default_rng(0)
    y = pd.Series(20 + 8 * np.sin(2 * np.pi * np.arange(len(idx)) / 365.25) + rng.normal(0, 2, len(idx)), index=idx)
    r = run(y, {}, RunSpec("daily", 14, "auto"))
    assert {c["model"] for c in r["comparison"]} == {*BASELINES, "ets", "sarima"}
    assert len(r["forecast"]) == 14 and r["forecast"][0][0] == "2024-01-01"


@pytest.mark.parametrize("model_id", ["lstm", "transformer"])
def test_deep_models_are_experimental_and_compared_with_baselines(model_id: str) -> None:
    r = run(seasonal(96), {}, RunSpec("monthly", 6, model_id))
    assert r["model"]["experimental"]
    assert any("experimental" in w for w in r["warnings"])
    assert {c["model"] for c in r["comparison"]} == {*BASELINES, model_id}


def test_no_skill_is_said_plainly() -> None:
    rng = np.random.default_rng(3)
    noise = pd.Series(rng.normal(0, 1, 150), index=pd.date_range("2000-01-01", periods=150, freq="MS"))
    r = run(noise, {}, RunSpec("monthly", 6, "naive"))
    assert r["metrics"]["skill"] < 0
    assert any("does not beat the long-term average" in w for w in r["warnings"])


# ---------------------------------------------------------------- drought classes


def test_spi_class_probabilities_sum_to_100_and_follow_the_median() -> None:
    idx = pd.date_range("2026-09-01", periods=3, freq="MS")
    out = class_probabilities(
        idx, np.array([-1.45, 0.3, -2.4]), np.array([-2.0, -0.5, -3.0]), np.array([-0.9, 1.1, -1.8])
    )
    assert [o["class"] for o in out] == ["D2", "None", "D4"]
    for o in out:
        assert abs(sum(o["probabilities"].values()) - 100) < 0.5
    p0 = out[0]["probabilities"]
    assert p0["D2"] > p0["D0"] and p0["D2"] > p0["None"]  # the median's class is likely, the far classes are not
    assert out[2]["probabilities"]["D4"] > 50
    assert out[1]["pDrought"] < 15 < out[0]["pDrought"]


def test_classified_runs_report_classes() -> None:
    spi = (seasonal(150, trend=0.0) - 10) / 4
    r = run(spi, {}, RunSpec("monthly", 6, "ets", classify=True))
    assert len(r["classes"]) == 6 and {"date", "class", "probabilities", "pDrought"} <= set(r["classes"][0])


# ---------------------------------------------------------------- page products: probabilities from a band


def test_probabilities_from_the_forecast_band() -> None:
    from app.forecasting.pages import band_probabilities, p_above, wqi_bands

    # symmetric band around the median: even odds of exceeding it; (almost) certain for a far-away threshold
    assert abs(p_above(10.0, 10.0, 8.0, 12.0) - 0.5) < 1e-9
    assert p_above(0.0, 10.0, 8.0, 12.0) > 0.999 and p_above(20.0, 10.0, 8.0, 12.0) < 0.001
    # the band's edges are the 10th / 90th percentiles
    assert abs(p_above(12.0, 10.0, 8.0, 12.0) - 0.10) < 1e-6
    probs = band_probabilities(75.0, 60.0, 90.0, wqi_bands())
    assert set(probs) == {"Excellent", "Good", "Fair", "Poor", "Very Poor"}
    assert abs(sum(probs.values()) - 100) < 1e-6 and probs["Good"] > 50 and probs["Very Poor"] < 1


def test_short_gaps_are_interpolated_longer_ones_stay() -> None:
    from app.forecasting.series import interpolate_short_gaps

    idx = pd.date_range("2024-01-01", periods=10, freq="MS")
    y = pd.Series([1.0, np.nan, 3.0, np.nan, np.nan, 6.0, np.nan, np.nan, np.nan, 10.0], index=idx)
    out = interpolate_short_gaps(y, 2)
    assert out.iloc[1] == 2.0 and out.iloc[3] == 4.0 and out.iloc[4] == 5.0  # gaps of 1 and 2 months filled
    assert out.iloc[6:9].isna().all()  # a 3-month gap stays missing
    assert interpolate_short_gaps(y, 0).equals(y)
