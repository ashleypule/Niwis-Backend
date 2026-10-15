"""Cascade tables carry the contract's row keys next to the backend's (BACKEND_ISSUES #2, #3).

Contract / mock rows: `{stage, metric, value (text), cls, scale, bar}`; `cls` is a class id on the palette `scale`
(so the frontend's `classify(scale, cls)` colours it), `bar` 0–100 from the class rank. The backend's own keys stay:
drought `class`, `severity`, `asOf` (and the number as `valueNum`), climate `signal`, `change`, `severity`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.thresholds import SEVERITY_ORDER, drought_class, severity_bar, storage_band, veg_class
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
CONTRACT_KEYS = {"stage", "metric", "value", "cls", "scale", "bar"}


def test_severity_bar_follows_the_class_rank() -> None:
    assert [severity_bar("drought", k) for k in SEVERITY_ORDER["drought"]] == [0, 20, 40, 60, 80, 100]
    assert [severity_bar("vci", k) for k in SEVERITY_ORDER["vci"]] == [0, 25, 50, 75, 100]
    assert [severity_bar("storage", k) for k in SEVERITY_ORDER["storage"]] == [0, 25, 50, 75, 100]
    assert severity_bar("drought", None) is None and severity_bar("risk", None) is None


@pytest.mark.parametrize(
    ("pct", "band"), [(10, "very-low"), (25, "low"), (49.9, "low"), (63.6, "moderate"), (90, "very-high")]
)
def test_storage_band(pct: float, band: str) -> None:
    assert storage_band(pct) == band


async def test_drought_cascade_rows(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    body = (await client.get("/api/v1/drought/table", params={"metric": "cascade", "municipality": "DC6"})).json()
    rows = {r["id"]: r for r in body["rows"]}
    for r in rows.values():
        assert CONTRACT_KEYS | {"class", "severity", "asOf", "valueNum"} <= set(r), r
    keys = {c["key"] for c in body["columns"]}
    assert {"stage", "value", "cls", "bar", "class", "severity", "asOf"} <= keys

    met = rows["meteorological"]
    ((spi,),) = fixture_db(
        """SELECT "SPI_6_Medium_Term_Hydrological_Impacts" FROM "6.2_rainfall_derived_drought_indices_master"
           WHERE "District_Name" = 'Namakwa' AND "Year" = 2026 AND "Month" = 8"""
    )
    assert met["valueNum"] == pytest.approx(float(spi), abs=0.005)
    assert met["value"] == f"SPI-6 {met['valueNum']:.2f}"
    cls = drought_class(met["valueNum"])
    assert (met["metric"], met["scale"]) == ("spi", "drought")
    assert met["cls"] == ("No drought" if cls == "None" else cls) and met["bar"] == severity_bar("drought", cls)

    agr = rows["agricultural"]
    assert (agr["metric"], agr["scale"], agr["cls"]) == ("vhi", "vci", veg_class(agr["valueNum"]))
    assert agr["value"] == f"VHI {agr['valueNum']:.0f} %" and agr["bar"] == severity_bar("vci", agr["cls"])

    hyd = rows["hydrological"]
    assert (hyd["metric"], hyd["scale"], hyd["cls"]) == ("storagePct", "storage", storage_band(hyd["valueNum"]))
    assert hyd["value"].endswith("% FSC") and hyd["bar"] == severity_bar("storage", hyd["cls"])

    soc = rows["socio-economic"]
    assert soc["value"] is soc["valueNum"] is soc["cls"] is soc["bar"] is None
    assert soc["metric"] == "settlementsAtRisk"
    assert "rows.socio-economic" in {u["field"] for u in body["unavailable"]}


async def test_climate_signal_chain_rows(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/climate/table", params={"metric": "signal-chain", "municipality": "DC6"})).json()
    for r in body["rows"]:
        assert CONTRACT_KEYS | {"signal", "change", "severity"} <= set(r)
        assert (r["stage"], r["value"], r["scale"]) == (r["signal"], r["change"], "risk")
        assert r["cls"] is r["bar"] is r["severity"] is None
    assert [r["metric"] for r in body["rows"]] == ["tmean", "et0", "rainfall", "runoff"]
    reasons = {u["field"]: u["reason"] for u in body["unavailable"]}
    assert set(reasons) == {"rows.severity", "rows.cls", "rows.bar"} and len(set(reasons.values())) == 1
