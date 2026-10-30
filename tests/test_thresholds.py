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
    assert [veg_class(v) for v in (95, 80, 79.9, 60, 40, 39.9, 20, 0)] == [  # spec §5.3 names
        "Excellent",
        "Excellent",
        "Good",
        "Good",
        "Fair",
        "Poor",
        "Poor",
        "Very Poor",
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


@pytest.mark.parametrize(
    ("ratio", "cls"),
    [
        (0.05, "Very Low"),
        (0.1, "Low"),
        (0.45, "Moderate"),
        (0.69, "High"),
        (1.0, "Very High"),
        (1.01, "Over-exploited"),
    ],
)
def test_exploitation_classes_match_ts(ratio: float, cls: str) -> None:
    from app.core.thresholds import exploitation_class

    assert exploitation_class(ratio) == cls
    ts = (Path(__file__).resolve().parent.parent / "reference" / "thresholds.ts").read_text(encoding="utf-8")
    for line in ("if (ei < 0.1) return 'Very Low';", "if (ei <= 1.0) return 'Very High';"):
        assert line in ts


def test_sans241_and_wqi_match_ts() -> None:
    from app.core.thresholds import sans241_pass, thresholds, wqi_class

    ts = (Path(__file__).resolve().parent.parent / "reference" / "thresholds.ts").read_text(encoding="utf-8")
    for lim in thresholds()["sans241"]:
        assert f"id: '{lim['id']}'" in ts
        if "max" in lim:  # the .ts writes 5.0 / 9.0 where JSON has 5 / 9
            assert re.search(rf"id: '{lim['id']}'.*max: {lim['max']:g}(\.0)?,", ts), lim["id"]
    assert not sans241_pass("nitrate", 11.5) and sans241_pass("nitrate", 11) and not sans241_pass("ph", 5.9)
    assert [wqi_class(v) for v in (95, 70, 50, 25, 10)] == ["Excellent", "Good", "Fair", "Poor", "Very Poor"]
    for b in ("if (wqi >= 90) return 'Excellent';", "if (wqi >= 25) return 'Poor';"):
        assert b in ts


def test_wq_guidelines_match_the_frontend_config() -> None:
    """The water-quality guidelines are the frontend's WQ_PARAMS (DWAF 1996 TWQR / SANS 241), value for value."""
    from app.core.thresholds import thresholds

    src = (
        Path(__file__).resolve().parent.parent.parent / "Niwis Frontend" / "src" / "mocks" / "generators" / "quality.ts"
    )
    if not src.exists():
        pytest.skip("frontend repository not next to the backend")
    ts = src.read_text(encoding="utf-8")
    for g in thresholds()["wq_guidelines"]["params"]:
        bounds = "".join(f", {k}: {g[k]:g}" for k in ("min", "max") if k in g)
        assert re.search(rf"id: '{g['id']}', label: '[^']*', unit: '[^']*'{re.escape(bounds)}(\.0)?,", ts), g["id"]


def test_compliance_bands_and_effluent_limits_match_ts() -> None:
    from app.core.thresholds import blue_drop_band, effluent_pass, green_drop_band, pes_class, rwqo_pass, thresholds

    assert [green_drop_band(v) for v in (95, 70, 50, 30, 10)] == ["Excellent", "Good", "Moderate", "Poor", "Critical"]
    assert [blue_drop_band(v) for v in (95, 90, 70, 50, 10)] == ["Excellent", "Good", "Moderate", "Poor", "Critical"]
    for line in (
        "if (score >= 90) return 'Excellent';",
        "if (score >= 95) return 'Excellent';",
        "if (score >= 30) return 'Poor';",
    ):
        assert line in TS
    for lim in thresholds()["effluent_limits"]["params"]:
        assert re.search(rf"(?<![A-Za-z]){lim['id']}: \{{ label: '{re.escape(lim['label'])}'", TS), lim["id"]
        if "max" in lim:
            assert re.search(rf"(?<![A-Za-z]){lim['id']}: \{{.*max: {lim['max']:g}(\.0)?", TS), lim["id"]
    assert effluent_pass("tss", 25) and not effluent_pass("tss", 25.1) and not effluent_pass("ph", 5.4)
    assert rwqo_pass("do", 5.0) and not rwqo_pass("do", 4.9) and not rwqo_pass("tp", 0.13)
    assert [pes_class(v) for v in (95, 85, 65, 45, 25, 5)] == ["A", "B", "C", "D", "E", "F"]
