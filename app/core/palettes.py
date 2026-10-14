"""Colours used in chart payloads (bands, series). Copied from the frontend palettes documented in
reference/DATA_DICTIONARY.md ("Colour scales"). Class boundaries come from thresholds.json, never from here."""

from __future__ import annotations

from app.core.thresholds import thresholds

DROUGHT = {
    "None": "#DCEFD8",
    "D0": "#FFF06A",
    "D1": "#FCD37F",
    "D2": "#FFAA00",
    "D3": "#E60000",
    "D4": "#730000",
}

# Neutral series colours (frontend picks its own when omitted; these match the mock's emphasis colours)
OBSERVED = "#13233A"
POSITIVE = "#2F7BC1"
NEGATIVE = "#D8B365"
NORMAL = "#7B8794"
YEAR_COLOURS = ["#1565C0", "#90A4AE", "#CFD8DC"]
SERIES = ["#1565C0", "#2E9E5B", "#F2A900", "#E95F35", "#6A1B9A", "#00838F"]


def drought_bands(floor: float = -3.0) -> list[dict[str, object]]:
    """Horizontal SPI bands D0–D4 for charts, built from the drought thresholds."""
    classes = thresholds()["drought"]["classes"]  # ordered D4 → D0, each with its upper bound `max`
    out: list[dict[str, object]] = []
    lower = floor
    for c in classes:
        out.append({"from": lower, "to": c["max"], "color": DROUGHT[c["id"]], "label": f"{c['id']} {c['label']}"})
        lower = c["max"]
    return out
