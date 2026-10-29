"""Forecasting API on the fixture database: models and targets, the run lifecycle, cache hits, limits, timeouts, 422s,
the monthly aggregation rule, and the page forecasts behind the contract forecast endpoints.

Fixture: City of Cape Town (CPT), Namakwa (DC6) and Alfred Nzo (DC44); monthly SPI 2000–2026, daily rainfall from 2024.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from app.forecasting.pages import PageForecasts
from app.forecasting.series import build_series, district_scope, target_from_catalogue
from app.main import create_app
from tests.conftest import make_settings, requires_db

pytestmark = [pytest.mark.db, requires_db]

S62 = "6.2_rainfall_derived_drought_indices_master"
SPI3 = "SPI_3_Short_Term_Agricultural_Drought"
R61 = "6.1_core_rainfall_variables_master"
CPT = {"level": "district", "id": "CPT"}


@contextlib.asynccontextmanager
async def app_client(**overrides: Any) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    settings = make_settings(**{"forecast_workers": 0, **overrides})
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app.state.niwis


async def finished(client: httpx.AsyncClient, run_id: str, timeout_s: float = 120) -> dict[str, Any]:
    for _ in range(int(timeout_s * 5)):
        r = await client.get(f"/api/v1/forecast/runs/{run_id}")
        assert r.status_code == 200
        body: dict[str, Any] = r.json()
        if body["status"] in ("done", "failed"):
            return body
        await asyncio.sleep(0.2)
    raise AssertionError("run did not finish")


def run_body(**kw: Any) -> dict[str, Any]:
    return {"table": S62, "column": SPI3, "scope": CPT, "model": "ets", "horizon": 6, **kw}


# ---------------------------------------------------------------- listings


async def test_models_listing() -> None:
    async with app_client() as (c, _):
        r = await c.get("/api/v1/forecast/models")
        assert r.status_code == 200
        body = r.json()
        ids = {m["id"] for m in body["models"]}
        assert {"naive", "ets", "sarima", "prophet", "random-forest", "lightgbm", "lstm", "transformer", "var"} <= ids
        assert [x["id"] for x in body["choices"]] == ["auto", "compare"]
        lstm = next(m for m in body["models"] if m["id"] == "lstm")
        assert lstm["experimental"] and lstm["minHistoryMonths"] == 60 and lstm["group"] == "deep"
        assert body["limits"]["monthlyHorizon"] == [1, 24] and body["limits"]["dailyHorizon"] == [1, 30]
        assert "not an official SAWS / DWS outlook" in body["disclaimer"]


async def test_targets_are_live_numeric_daily_or_monthly_columns() -> None:
    async with app_client() as (c, _):
        r = await c.get("/api/v1/forecast/targets", params={"module": "rainfall"})
        assert r.status_code == 200
        cols = {(t["table"], x["column"]): x for t in r.json()["tables"] for x in t["columns"]}
        spi = cols[(S62, SPI3)]
        assert spi["classified"] and spi["resolution"] == "monthly" and spi["aggregate"] == "mean"
        rain = cols[(R61, "Daily_Rainfall_Total_mm")]
        assert (
            rain["aggregate"] == "sum"
            and rain["monthlyUnit"] == "mm/month"
            and rain["resolutions"] == ["monthly", "daily"]
        )
        assert (R61, "Seasonal_Total_OND_mm") not in cols  # annual
        assert ("meteorological_drought_master", "daily_rainfall_mm_day") not in cols  # duplicate
        assert all(x["unit"] != "class" for x in cols.values())
        assert (await c.get("/api/v1/forecast/targets", params={"module": "nope"})).status_code == 400


# ---------------------------------------------------------------- runs


async def test_run_lifecycle_and_cache_hit() -> None:
    async with app_client() as (c, _):
        r = await c.post("/api/v1/forecast/runs", json=run_body())
        assert r.status_code == 202, r.text
        first = r.json()
        assert first["cached"] is False and r.headers["cache-control"] == "no-store"
        body = await finished(c, first["runId"])
        assert body["status"] == "done", body
        res = body["result"]
        assert res["model"]["id"] == "ets" and len(res["forecast"]) == 6
        assert res["dataAsOf"] == "2026-08-01"  # September 2026 is incomplete in the fixture (6.1 ends 6 Sep)
        assert res["forecastStart"] == "2026-09-01"
        assert res["disclaimer"].startswith("Statistical forecast from historical data")
        assert res["target"]["classified"] and len(res["classes"]) == 6
        assert {"mae", "rmse", "mase", "skill", "coverage", "smape"} <= set(res["metrics"])
        assert {x["model"] for x in res["comparison"]} >= {"naive", "seasonal-naive", "climatology", "moving-average"}
        assert res["sources"] == [f"{S62}.{SPI3}"] and res["scope"]["id"] == "CPT"
        again = await c.post("/api/v1/forecast/runs", json=run_body())
        assert again.status_code == 202 and again.json() == {**first, "cached": True, "status": "done"}


async def test_short_series_is_a_422_with_the_reason() -> None:
    async with app_client() as (c, _):
        r = await c.post(
            "/api/v1/forecast/runs", json=run_body(table=R61, column="Daily_Rainfall_Total_mm", model="auto")
        )
        assert r.status_code == 422
        assert r.json()["code"] == "UNPROCESSABLE" and "At least 36 months" in r.json()["error"]


async def test_bad_requests() -> None:
    async with app_client() as (c, _):
        bad = [
            run_body(column="SSI_3_dimensionless"),  # unknown
            run_body(table="satellite_remote_sensing", column="evi"),  # not a column / withheld
            run_body(table="meteorological_drought_master", column="spi_3"),  # duplicate
            run_body(model="garch"),
            run_body(horizon=25),
            run_body(resolution="daily"),  # SPI-3 is monthly
            run_body(scope={"level": "province"}),  # id missing
            run_body(inputs=[{"table": S62, "column": SPI3}]),  # the target itself
        ]
        for b in bad:
            r = await c.post("/api/v1/forecast/runs", json=b)
            assert r.status_code in (400, 404), (b, r.status_code, r.text)
        assert (await c.post("/api/v1/forecast/runs", json={**run_body(), "extra": 1})).status_code == 400
        assert (await c.get("/api/v1/forecast/runs/0123abcd")).status_code == 404


async def test_per_ip_limit_counts_new_runs_only() -> None:
    async with app_client(forecast_runs_per_hour=1) as (c, _):
        assert (await c.post("/api/v1/forecast/runs", json=run_body(model="naive"))).status_code == 202
        r = await c.post("/api/v1/forecast/runs", json=run_body(model="climatology"))
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
        assert (await c.post("/api/v1/forecast/runs", json=run_body(model="naive"))).json()["cached"] is True


async def test_global_queue_limit() -> None:
    async with app_client(forecast_queue_limit=0) as (c, _):
        r = await c.post("/api/v1/forecast/runs", json=run_body(model="naive"))
        assert r.status_code == 503 and r.json()["code"] == "BUSY" and r.headers["retry-after"]


async def test_worker_process_success_and_timeout() -> None:
    async with app_client(forecast_workers=1, forecast_timeout_s=90) as (c, _):
        r = await c.post("/api/v1/forecast/runs", json=run_body(model="climatology"))
        body = await finished(c, r.json()["runId"])
        assert body["status"] == "done", body
    async with app_client(forecast_workers=1, forecast_timeout_s=1) as (c, _):
        r = await c.post("/api/v1/forecast/runs", json=run_body(model="compare"))
        body = await finished(c, r.json()["runId"])
        assert body["status"] == "failed" and body["error"]["code"] == "TIMEOUT"
        retry = await c.post("/api/v1/forecast/runs", json=run_body(model="compare"))
        assert retry.json()["cached"] is False  # a timeout is not cached


async def test_runs_expire() -> None:
    async with app_client(forecast_retention_h=0) as (c, _):
        r = await c.post("/api/v1/forecast/runs", json=run_body(model="naive"))
        rid = r.json()["runId"]
        for _ in range(100):
            g = await c.get(f"/api/v1/forecast/runs/{rid}")
            if g.status_code == 404:
                break
            await asyncio.sleep(0.1)
        assert g.status_code == 404


# ---------------------------------------------------------------- aggregation rule


async def test_monthly_rainfall_is_the_sum_of_daily_rainfall(
    fixture_db: Callable[[str], list[tuple[Any, ...]]],
) -> None:
    async with app_client() as (_, state):
        t = target_from_catalogue(R61, "Daily_Rainfall_Total_mm")
        assert t.aggregate == "sum" and t.unit == "mm/month"
        s = (await build_series(state, t, district_scope("CPT"), "monthly")).values.dropna()
    rows = fixture_db(
        f'SELECT date_trunc(\'month\', "Date"::date)::date, sum("Daily_Rainfall_Total_mm"), count(*) FROM "{R61}" '
        'WHERE "District_Name" = \'City of Cape Town\' AND "Date"::date <= CURRENT_DATE GROUP BY 1 ORDER BY 1'
    )
    sql = {str(m): (float(v), n) for m, v, n in rows}
    assert len(s) >= 30
    for t_, v in s.items():
        total, _ = sql[str(t_.date())]
        assert abs(v - total) < 1e-6, t_
    # a temperature (state) is averaged, never summed
    tmax = target_from_catalogue("4_1_temperature_change_master", "Tmax_C")
    assert tmax.aggregate == "mean" and tmax.unit == "°C"


# ---------------------------------------------------------------- page forecasts


async def test_page_forecasts_feed_the_contract_endpoints() -> None:
    async with app_client() as (c, state):
        pf: PageForecasts = state.page_forecasts
        pf.model, pf.only = "seasonal-naive", {"spi-3", "spi-6", "spei-3", "drought-area", "rainfall"}
        pf.scopes = lambda: [district_scope("CPT")]  # type: ignore[method-assign]
        before = await c.get("/api/v1/drought/forecast", params={"metric": "spi", "municipality": "CPT"})
        assert before.status_code == 501 and before.headers["cache-control"] == "no-store"
        assert await pf.refresh() == 3  # SPEI-3 and rainfall: 32 months in the fixture (daily tables from 2024)
        r = await c.get("/api/v1/drought/forecast", params={"metric": "spi", "municipality": "CPT", "horizon": "6"})
        assert r.status_code == 200, r.text
        body = r.json()
        keys = [s["key"] for s in body["series"]]
        assert keys == ["band", "obs", "median"]
        median = body["series"][2]["data"]
        assert median[0][0] == body["asOf"] == "2026-08-01" and len(median) == 7  # joined to the last observation
        f = body["forecast"]
        assert f["label"] == "Statistical forecast" and f["model"] == "seasonal-naive" and f["dataAsOf"] == "2026-08-01"
        assert "not an official SAWS / DWS outlook" in f["disclaimer"] and "not an official" in body["note"]
        assert f["studio"].startswith("/ai?")
        six = await c.get("/api/v1/drought/forecast", params={"metric": "spi", "municipality": "CPT", "scale": "6"})
        assert six.json()["series"][1]["data"] != body["series"][1]["data"]
        tbl = await c.get("/api/v1/drought/table", params={"metric": "forecast-indicators", "municipality": "CPT"})
        assert tbl.status_code == 200
        assert [r_["id"] for r_ in tbl.json()["rows"]] == ["spi-3", "spi-6", "spei-3", "drought-area"]
        cp = await c.get("/api/v1/drought/breakdown", params={"metric": "class-probability", "municipality": "CPT"})
        assert cp.status_code == 200
        for i in range(len(cp.json()["categories"])):
            assert abs(sum(s["values"][i] for s in cp.json()["series"]) - 100) < 1.5
        rain = await c.get("/api/v1/rainfall/forecast", params={"metric": "rainfall", "municipality": "CPT"})
        assert rain.status_code == 501 and "At least 36 months" in rain.json()["detail"]  # the page shows why
        spei = next(r_ for r_ in tbl.json()["rows"] if r_["id"] == "spei-3")
        assert spei["h3"] is None and any(u["field"] == "rows.spei-3" for u in tbl.json()["unavailable"])
        area = await c.get("/api/v1/drought/forecast", params={"metric": "area", "municipality": "CPT"})
        assert all(0 <= v[1] <= 100 for v in area.json()["series"][2]["data"])
        # not precomputed for this scope / not computed at all / not a product of a forecast variable
        assert (await c.get("/api/v1/drought/forecast", params={"metric": "spi"})).status_code == 501
        assert (
            await c.get("/api/v1/climate/forecast", params={"metric": "hot-days", "municipality": "CPT"})
        ).status_code == 501
        assert (await c.get("/api/v1/drought/forecast", params={"metric": "population"})).status_code == 501
        assert (await c.get("/api/v1/drought/map", params={"layer": "outlook-1"})).status_code == 501
        assert (await c.get("/api/v1/ai/forecast", params={"metric": "reservoir"})).status_code == 501
        status = (await c.get("/api/v1/forecast/pages")).json()
        assert {i["variable"] for i in status["items"]} == pf.only
