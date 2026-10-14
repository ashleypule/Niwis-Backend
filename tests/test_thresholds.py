"""Classification thresholds: boundary behaviour and sync with the frontend's reference/thresholds.ts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.thresholds import (
    borehole_status,
    drought_class,
    drought_label,
    spi_category,
    storage_alert_level,
    thresholds,
    veg_class,
)

TS = (Path(__file__).resolve().parent.parent / "reference" / "thresholds.ts").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("spi", "expected"),
    [
        (None, "None"),
        (float("nan"), "None"),
        (0.0, "None"),
        (-0.49, "None"),
        (-0.495, "None"),  # JS Math.round(-49.5) = -49 → -0.49
        (-0.5, "D0"),
        (-0.79, "D0"),
        (-0.8, "D1"),
        (-1.29, "D1"),
        (-1.3, "D2"),
        (-1.59, "D2"),
        (-1.6, "D3"),
        (-1.995, "D3"),  # rounds to -1.99 like the frontend
        (-1.99, "D3"),
        (-2.0, "D4"),
        (-3.0, "D4"),
        (2.5, "None"),
    ],
)
def test_drought_class_boundaries(spi: float | None, expected: str) -> None:
    assert drought_class(spi) == expected


def test_drought_labels() -> None:
    assert drought_label("None") == "No drought"
    assert drought_label("D2") == "D2 Severe Drought"


@pytest.mark.parametrize(
    ("spi", "label"),
    [
        (2.0, "Extremely wet"),
        (1.5, "Very wet"),
        (1.0, "Moderately wet"),
        (0.0, "Near normal"),
        (-1.0, "D1 Moderate Drought"),
    ],
)
def test_spi_category(spi: float, label: str) -> None:
    assert spi_category(spi) == label


def test_other_classes() -> None:
    assert [veg_class(v) for v in (95, 80, 79.9, 60, 40, 39.9, 20, 0)] == [
        "Very Healthy",
        "Very Healthy",
        "Healthy",
        "Healthy",
        "Moderate",
        "Stressed",
        "Stressed",
        "Very Stressed",
    ]
    assert [storage_alert_level(v) for v in (60, 49.9, 29.9, 14.9)] == ["None", "L1", "L2", "L3"]
    assert [borehole_status(v) for v in (0, -0.5, -1.0, -1.6)] == ["Normal", "Watch", "Low", "Critical"]


# ---------------------------------------------------------------- sync with reference/thresholds.ts


def test_drought_thresholds_match_ts() -> None:
    ts = {cls: float(v) for v, cls in re.findall(r"if \(v <= (-?[\d.]+)\) return '(D\d)'", TS)}
    js = {c["id"]: c["max"] for c in thresholds()["drought"]["classes"]}
    assert ts == js


def test_storage_alerts_match_ts() -> None:
    m = re.search(r"STORAGE_ALERTS = \{ L1: (\d+), L2: (\d+), L3: (\d+) \}", TS)
    assert m is not None
    a = thresholds()["storage_alerts"]
    assert (a["L1"], a["L2"], a["L3"]) == tuple(int(x) for x in m.groups())


def test_veg_and_borehole_match_ts() -> None:
    veg_ts = [(float(v), c) for v, c in re.findall(r"if \(vci >= (\d+)\) return '([\w ]+)'", TS)]
    veg_js = [(float(c["min"]), c["id"]) for c in thresholds()["vegetation"]["classes"] if c["min"] is not None]
    assert veg_ts == veg_js
    bh_ts = [(float(v), c) for v, c in re.findall(r"if \(sgi <= (-?[\d.]+)\) return '(\w+)'", TS)]
    bh_js = [(c["max"], c["id"]) for c in thresholds()["borehole_status_sgi"]["classes"]]
    assert bh_ts == bh_js
