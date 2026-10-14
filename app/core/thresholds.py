"""Classification thresholds — the only place classes are derived. Values come from thresholds.json, which mirrors the
frontend's reference/thresholds.ts (checked by tests/test_thresholds.py)."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

PATH = Path(__file__).with_name("thresholds.json")


@lru_cache
def thresholds() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(PATH.read_text(encoding="utf-8"))
    return data


def _round_half_away(x: float, dp: int) -> float:
    """JavaScript Math.round(x * 100) / 100 semantics (rounds .5 towards +∞), to match the frontend exactly."""
    f = 10**dp
    return float(math.floor(x * f + 0.5) / f)


def drought_class(spi: float | None) -> str:
    """SPI / SPEI / SSI / SGI → 'None' | 'D0' … 'D4' (spec §3.4)."""
    if spi is None or (isinstance(spi, float) and math.isnan(spi)):
        return "None"
    cfg = thresholds()["drought"]
    v = _round_half_away(spi, cfg["round_dp"])
    for c in cfg["classes"]:  # ordered D4 → D0
        if v <= c["max"]:
            return str(c["id"])
    return "None"


def drought_label(class_id: str) -> str:
    cfg = thresholds()["drought"]
    if class_id == "None":
        return str(cfg["none"]["label"])
    for c in cfg["classes"]:
        if c["id"] == class_id:
            return f"{class_id} {c['label']}"
    raise KeyError(class_id)


def spi_category(spi: float) -> str:
    cfg = thresholds()["spi_wet"]
    for c in cfg["classes"]:
        if spi >= c["min"]:
            return str(c["label"])
    if spi > cfg["near_normal_above"]:
        return "Near normal"
    return drought_label(drought_class(spi))


def veg_class(index_pct: float) -> str:
    for c in thresholds()["vegetation"]["classes"]:
        if c["min"] is None or index_pct >= c["min"]:
            return str(c["id"])
    raise AssertionError("unreachable")


def storage_alert_level(pct_fsc: float) -> str:
    a = thresholds()["storage_alerts"]
    if pct_fsc < a["L3"]:
        return "L3"
    if pct_fsc < a["L2"]:
        return "L2"
    if pct_fsc < a["L1"]:
        return "L1"
    return "None"


def borehole_status(sgi: float) -> str:
    cfg = thresholds()["borehole_status_sgi"]
    for c in cfg["classes"]:
        if sgi <= c["max"]:
            return str(c["id"])
    return str(cfg["otherwise"])
