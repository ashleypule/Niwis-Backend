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


NDVI_TREND = ["#8C510A", "#D8B365", "#F5F5F5", "#5AB4AC", "#01665E"]  # browning → greening (ndviTrend palette)


def _nice(x: float) -> float:
    """Round a positive break to 2 significant figures (0.01234 → 0.012)."""
    if x <= 0:
        return 0.0
    return float(f"{x:.2g}")


def symmetric_legend(
    values: list[float], colors: list[str], unit: str, low: str, high: str, q: tuple[float, float] = (0.4, 0.8)
) -> list[dict[str, object]]:
    """Five classes symmetric around 0 from the data: breaks ±a, ±b at the `q` quantiles of |value| (rounded to 2
    significant figures). With fixed breaks too wide for the data, every district lands in the middle class."""
    import numpy as np

    mags = np.abs(np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float))
    if mags.size == 0:
        return []
    a, b = (_nice(float(np.quantile(mags, x))) for x in q)
    if b <= a:
        b = _nice(a * 2) if a > 0 else 0.0
    edges: list[tuple[float | None, float | None, str]] = [
        (None, -b, f"< −{b:g} {unit} ({low})"),
        (-b, -a, f"−{b:g} to −{a:g} {unit}"),
        (-a, a, f"−{a:g} to +{a:g} {unit} (stable)"),
        (a, b, f"+{a:g} to +{b:g} {unit}"),
        (b, None, f"> +{b:g} {unit} ({high})"),
    ]
    pairs = zip(edges, colors, strict=True)
    return [{"from": lo, "to": hi, "color": col, "label": lab} for (lo, hi, lab), col in pairs]


def drought_bands(floor: float = -3.0) -> list[dict[str, object]]:
    """Horizontal SPI bands D0–D4 for charts, built from the drought thresholds."""
    classes = thresholds()["drought"]["classes"]  # ordered D4 → D0, each with its upper bound `max`
    out: list[dict[str, object]] = []
    lower = floor
    for c in classes:
        out.append({"from": lower, "to": c["max"], "color": DROUGHT[c["id"]], "label": f"{c['id']} {c['label']}"})
        lower = c["max"]
    return out
