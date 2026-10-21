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


def drought_class_or_none(spi: float | None) -> str | None:
    """`drought_class`, but None for a missing value: "None" is the *No drought* class and must never stand in for
    "no data"."""
    if spi is None or (isinstance(spi, float) and math.isnan(spi)):
        return None
    return drought_class(spi)


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


def storage_band(pct_fsc: float) -> str:
    """% FSC → storage palette class id (`very-low` … `very-high`), < comparisons as in thresholds.ts."""
    for b in thresholds()["storage_bands"]:
        if b["max"] is None or pct_fsc < b["max"]:
            return str(b["id"])
    raise AssertionError("unreachable")


def exploitation_class(ratio: float) -> str:
    """Exploitation index (abstraction / recharge, a ratio — the database stores it in %) → class (spec §11.3,
    thresholds.ts `exploitationClass`)."""
    cfg = thresholds()["exploitation"]
    for c in cfg["classes"]:
        if ratio < c["max"] or (c.get("inclusive") and ratio <= c["max"]):
            return str(c["id"])
    return str(cfg["otherwise"])


def wqi_class(wqi: float) -> str:
    cfg = thresholds()["wqi"]
    for c in cfg["classes"]:
        if wqi >= c["min"]:
            return str(c["id"])
    return str(cfg["otherwise"])


def sans241_pass(param: str, value: float) -> bool:
    """Pass / fail of one value against SANS 241:2015 (spec §14.1); unknown determinands pass."""
    lim = next((x for x in thresholds()["sans241"] if x["id"] == param), None)
    if lim is None:
        return True
    return not (("min" in lim and value < lim["min"]) or ("max" in lim and value > lim["max"]))


# Classes of each palette scale, least to most severe (cascade `bar`)
SEVERITY_ORDER: dict[str, list[str]] = {
    "drought": ["None", "D0", "D1", "D2", "D3", "D4"],
    "vci": ["Excellent", "Good", "Fair", "Poor", "Very Poor"],  # spec §5.3 vegetation health classes
    "storage": ["very-high", "high", "moderate", "low", "very-low"],
    "status": ["Normal", "Watch", "Low", "Critical"],  # borehole SGI status (thresholds.ts boreholeStatus)
    "exploitation": ["Very Low", "Low", "Moderate", "High", "Very High", "Over-exploited"],
    "wqi": ["Excellent", "Good", "Fair", "Poor", "Very Poor"],
}


def severity_bar(scale: str, class_id: str | None) -> float | None:
    """Bar length 0–100 from a class: its rank on the scale, least severe = 0, most severe = 100 (evenly spaced:
    drought None/D0…D4 = 0/20/…/100; VCI/VHI classes and storage bands = 0/25/50/75/100). None without a class."""
    order = SEVERITY_ORDER.get(scale, [])
    if class_id is None or class_id not in order:
        return None
    return round(order.index(class_id) / (len(order) - 1) * 100, 1)
