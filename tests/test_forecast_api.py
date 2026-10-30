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
        assert [x["id"] for x in body["choices"]] == ["auto", "compare", "auto-fast"]
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


async def wait_for(pf: PageForecasts, key: tuple[str, str], timeout_s: float = 120) -> None:
    """Wait until an on-demand entry has finished (done or failed)."""
    for _ in range(int(timeout_s * 5)):
        e = pf.entries.get(key)
        if e is not None and e.status != "running":
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"{key} did not finish")


async def test_page_forecasts_feed_the_contract_endpoints() -> None:
    async with app_client(forecast_page_on_demand=False) as (c, state):
        pf: PageForecasts = state.page_forecasts
        pf.model, pf.only = "seasonal-naive", {"spi-3", "spi-6", "spei-3", "drought-area", "rainfall"}
        pf.active = True  # the rounds are driven by hand here (FORECAST_PAGES is off in the test settings)
        pf.scopes = lambda: [district_scope("CPT")]  # type: ignore[method-assign]
        pf.district_scopes = lambda: []  # type: ignore[method-assign]
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
        # early warning: current class, class in 3 months, its probability; the area row's confidence is the
        # probability of the forecast direction; SPEI-3 (no forecast) keeps its row with the reason
        ew = await c.get("/api/v1/drought/table", params={"metric": "early-warning", "municipality": "CPT"})
        assert ew.status_code == 200, ew.text
        rows = {r_["id"]: r_ for r_ in ew.json()["rows"]}
        assert set(rows) == {"spi-3", "spi-6", "spei-3", "drought-area"}
        assert rows["spi-3"]["forecastClass"] in {"None", "D0", "D1", "D2", "D3", "D4"}
        assert 0 <= rows["spi-3"]["confidence"] <= 100 and 0 <= rows["drought-area"]["confidence"] <= 100
        assert "% of the area" in rows["drought-area"]["status"]
        assert rows["spei-3"]["confidence"] is None
        assert any(u["field"] == "rows.spei-3" for u in ew.json()["unavailable"])
        assert (
            "early-warning" in (await c.get("/api/v1/ai/table", params={"metric": "early-warning"})).text
        )  # national: 501
        # recovery: expected class and P(no drought) 3 / 6 / 12 / 24 months ahead from the SPI-3 classes
        rec = await c.get("/api/v1/drought/table", params={"metric": "recovery", "municipality": "CPT"})
        assert rec.status_code == 200, rec.text
        assert [r_["id"] for r_ in rec.json()["rows"]] == ["h3", "h6", "h12", "h24"]
        for r_ in rec.json()["rows"]:
            assert abs(r_["probability"] + r_["pDrought"] - 100) < 0.2 and r_["conditionClass"] in {
                *"",
                "None",
                "D0",
                "D1",
                "D2",
                "D3",
                "D4",
            }
        # not precomputed for this scope / not computed at all / not a product of a forecast variable
        assert (await c.get("/api/v1/drought/forecast", params={"metric": "spi"})).status_code == 501
        assert (await c.get("/api/v1/drought/timeseries", params={"metric": "recovery-province"})).status_code == 501
        hot = await c.get("/api/v1/climate/forecast", params={"metric": "hot-days", "province": "GP"})
        assert hot.status_code == 501 and "nine provinces" in hot.json()["detail"]  # on demand is off here
        assert (await c.get("/api/v1/drought/forecast", params={"metric": "population"})).status_code == 501
        # the national outlook map serves the districts that have a forecast (here Cape Town) and nulls the rest
        om = await c.get("/api/v1/drought/map", params={"layer": "outlook-1"})
        assert om.status_code == 200 and om.json()["values"]["CPT"] is not None and om.json()["values"]["DC1"] is None
        assert (await c.get("/api/v1/ai/forecast", params={"metric": "reservoir"})).status_code == 501
        # the AI page's KPIs (D47): from the page forecasts of the scope, the derived alerts and the run log
        s = await c.get("/api/v1/ai/summary", params={"municipality": "CPT"})
        assert s.status_code == 200, s.text
        k = {x["id"]: x for x in s.json()["kpis"]}
        assert k["models"]["value"] == 1 and "seasonal" not in k["models"]["sublabel"]  # one model: seasonal-naive
        assert k["skill"]["value"] is not None and 0 <= k["drought6"]["value"] <= 100
        assert k["reservoir"]["value"] is None and "D2" in k["reservoir"]["unavailableReason"]
        assert k["warnings"]["value"] >= 0 and k["runs"]["value"] >= 0
        nat = (await c.get("/api/v1/ai/summary")).json()  # nothing computed for South Africa here
        kn = {x["id"]: x for x in nat["kpis"]}
        assert kn["models"]["value"] is None and kn["warnings"]["value"] is not None
        assert any(u["field"] == "kpis.models" for u in nat["unavailable"])
        imp = await c.get("/api/v1/ai/breakdown", params={"metric": "importance"})
        if imp.status_code == 200:
            body = imp.json()
            assert len(body["categories"]) == 8 and abs(sum(body["series"][0]["values"]) - 100) < 1
        else:  # the fixture's national SPI-3 series is too short for the lag features
            assert imp.status_code == 501 and "drought model" in imp.json()["detail"], imp.text
        assert (await c.get("/api/v1/ai/breakdown", params={"metric": "reservoir"})).status_code == 501
        status = (await c.get("/api/v1/forecast/pages")).json()
        assert {i["variable"] for i in status["items"]} == pf.only


async def test_district_round_feeds_outlook_maps_and_probabilities() -> None:
    """The quick-model district round: outlook maps per district (nulls with the reason where a district's forecast
    is missing), vegetation recovery probabilities, the water-quality forecasts, band outlook and indicator table."""
    async with app_client(forecast_page_on_demand=False) as (c, state):
        pf: PageForecasts = state.page_forecasts
        pf.fast = "seasonal-naive"
        pf.only = {"spi-3", "spi-6", "ndvi", "vci", "wqi", "nitrate", "phosphate", "turbidity", "ecoli"}
        pf.outlook_vars = tuple(sorted(pf.only))
        pf.active = True
        pf.scopes = lambda: []  # type: ignore[method-assign]
        pf.district_scopes = lambda: [district_scope(x) for x in ("DC44", "CPT", "DC6")]  # type: ignore[method-assign]
        assert await pf.refresh() == 27, [(k, e.error) for k, e in pf.entries.items() if e.status != "done"]
        assert all(e.status == "done" for e in pf.entries.values()), [e.error for e in pf.entries.values()]
        # SPI-3 outlook for the Western Cape: Cape Town has a value, the other WC districts are null (not computed)
        m = await c.get("/api/v1/drought/map", params={"layer": "outlook-2", "province": "WC"})
        assert m.status_code == 200, m.text
        body = m.json()
        assert body["geometry"] == "districts" and isinstance(body["values"]["CPT"], float)
        assert body["values"]["DC1"] is None and body["title"].startswith("SPI-3 outlook – ")
        assert body["forecast"]["model"] == "auto" and body["date"] == "2026-10-01"  # data to Aug 2026: +2 months
        six = await c.get("/api/v1/drought/map", params={"layer": "forecast-6", "municipality": "CPT"})
        assert six.status_code == 200 and six.json()["date"] == "2027-02-01" and set(six.json()["values"]) == {"CPT"}
        assert (
            await c.get("/api/v1/rainfall/map", params={"layer": "forecast-1", "province": "WC"})
        ).status_code == 501
        # NDVI forecast and anomaly maps
        nd = await c.get("/api/v1/vegetation/map", params={"layer": "forecast-ndvi", "municipality": "CPT"})
        assert nd.status_code == 200 and -1 <= nd.json()["values"]["CPT"] <= 1
        an = await c.get("/api/v1/vegetation/map", params={"layer": "forecast-anomaly", "municipality": "CPT"})
        assert an.status_code == 200 and an.json()["scale"] == "ndviAnomaly" and abs(an.json()["values"]["CPT"]) < 1
        # recovery probability: per district of the scope (a province), per province for South Africa (none yet)
        rp = await c.get("/api/v1/vegetation/forecast", params={"metric": "recovery-probability", "province": "WC"})
        assert rp.status_code == 200, rp.text
        data = dict(rp.json()["series"][0]["data"])
        assert 0 <= data["City of Cape Town"] <= 100 and sum(v is None for v in data.values()) == len(data) - 1
        assert (
            await c.get("/api/v1/vegetation/forecast", params={"metric": "recovery-probability"})
        ).status_code == 501
        # water quality: WQI, parameters, E. coli, the band outlook, the indicator table and the WQI map
        wq = await c.get("/api/v1/water-quality/forecast", params={"metric": "wqi", "municipality": "DC44"})
        assert wq.status_code == 200, wq.text
        assert all(0 <= v[1] <= 100 for v in wq.json()["series"][2]["data"]) and wq.json()["forecast"]["studio"] is None
        pr = await c.get("/api/v1/water-quality/forecast", params={"metric": "params", "municipality": "DC44"})
        assert pr.status_code == 200, pr.text
        assert [s["key"] for s in pr.json()["series"]] == [
            "nitrate",
            "nitrate-fc",
            "phosphate",
            "phosphate-fc",
            "turbidity",
            "turbidity-fc",
        ]
        assert pr.json()["series"][4]["axis"] == 1  # turbidity on the NTU axis
        ec = await c.get("/api/v1/water-quality/forecast", params={"metric": "ecoli-catchment", "municipality": "DC44"})
        assert ec.status_code == 200 and all(v[1] >= 0 for v in ec.json()["series"][2]["data"])
        ro = await c.get("/api/v1/water-quality/breakdown", params={"metric": "risk-outlook", "municipality": "DC44"})
        assert ro.status_code == 200, ro.text
        assert len(ro.json()["categories"]) == 12 and next(s["name"] for s in ro.json()["series"]) == "Excellent"
        for i in range(12):
            assert abs(sum(s["values"][i] for s in ro.json()["series"]) - 100) < 1.5
        ti = await c.get(
            "/api/v1/water-quality/table", params={"metric": "forecast-indicators", "municipality": "DC44"}
        )
        assert ti.status_code == 200 and [r_["id"] for r_ in ti.json()["rows"]] == [
            "wqi",
            "nitrate",
            "phosphate",
            "turbidity",
            "ecoli",
        ]
        wm = await c.get("/api/v1/water-quality/map", params={"layer": "forecast-wqi", "municipality": "DC44"})
        assert wm.status_code == 200 and 0 <= wm.json()["values"]["DC44"] <= 100
        q = await c.get("/api/v1/ai/forecast", params={"metric": "quality", "municipality": "DC44"})
        assert q.status_code == 200 and q.json()["id"] == "quality-forecast"


async def test_page_forecasts_on_demand_for_any_area() -> None:
    """A (variable, area) pair outside the round is computed the first time its tab is opened: 501 "being computed"
    (no-store) at once, 200 once the run finishes; a series the data cannot support keeps the reason; the entries
    are dropped when the data change."""
    async with app_client(forecast_page_on_demand=True) as (c, state):
        pf: PageForecasts = state.page_forecasts
        pf.fast = "seasonal-naive"
        pf.scopes = lambda: []  # type: ignore[method-assign]
        pf.district_scopes = lambda: []  # type: ignore[method-assign]
        q = {"metric": "spi", "scale": "3", "municipality": "DC44"}
        first = await c.get("/api/v1/rainfall/forecast", params=q)
        assert first.status_code == 501 and "being computed" in first.json()["detail"]
        assert first.headers["cache-control"] == "no-store"
        await wait_for(pf, ("spi-3", "municipality=DC44"))
        again = await c.get("/api/v1/rainfall/forecast", params=q)
        assert again.status_code == 200, again.text
        assert again.json()["forecast"]["model"] == "seasonal-naive" and again.json()["forecast"]["studio"]
        # the Runoff page's modelled flow: a derived series (depth × area), 32 months in the fixture → the reason
        flow = {"metric": "flow", "municipality": "DC44"}
        assert (await c.get("/api/v1/runoff/forecast", params=flow)).status_code == 501
        await wait_for(pf, ("runoff-flow", "municipality=DC44"))
        r = await c.get("/api/v1/runoff/forecast", params=flow)
        assert r.status_code == 501 and "At least 36 months" in r.json()["detail"]
        fp = await c.get("/api/v1/runoff/forecast", params={"metric": "flood-probability", "municipality": "DC44"})
        assert fp.status_code == 501 and "At least 36 months" in fp.json()["detail"]
        items = (await c.get("/api/v1/forecast/pages")).json()["items"]
        assert {(i["variable"], i["scope"]) for i in items} >= {
            ("spi-3", "municipality=DC44"),
            ("runoff-flow", "municipality=DC44"),
        }
        # new data: the on-demand entries are dropped (recomputed when their tab is opened again)
        pf.newest = None
        await pf.refresh()
        assert ("spi-3", "municipality=DC44") not in pf.entries
        # a WMA works the same way
        wma = await c.get("/api/v1/drought/forecast", params={"metric": "spi", "wma": "WMA9"})
        assert wma.status_code == 501 and "being computed" in wma.json()["detail"]
        await wait_for(pf, ("spi-3", "wma=WMA9"))
        # a transient failure (a database timeout while the series is read) is retried when the tab is opened again
        import app.forecasting.service as svc

        real, calls = svc.prepare, {"n": 0}

        async def flaky(*a: Any, **kw: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("canceling statement due to statement timeout")
            return await real(*a, **kw)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(svc, "prepare", flaky)
            q6 = {"metric": "spi", "scale": "6", "municipality": "DC6"}
            assert (await c.get("/api/v1/drought/forecast", params=q6)).status_code == 501
            await wait_for(pf, ("spi-6", "municipality=DC6"))
            assert pf.entries[("spi-6", "municipality=DC6")].retry
            r = await c.get("/api/v1/drought/forecast", params=q6)
            assert r.status_code == 501 and "being computed" in r.json()["detail"]
            await wait_for(pf, ("spi-6", "municipality=DC6"))
            assert (await c.get("/api/v1/drought/forecast", params=q6)).status_code == 200
