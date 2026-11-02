"""Volume-profile horizontal zones for observation only (never a signal source)."""
from __future__ import annotations

import math
from typing import Any


def _get(row: Any, *names: str) -> float | None:
    for name in names:
        value = row.get(name) if isinstance(row, dict) else getattr(row, name, None)
        if value is not None:
            try:
                number = float(value)
                if math.isfinite(number):
                    return number
            except (TypeError, ValueError):
                continue
    return None


def is_enabled(value: str | None = None) -> bool:
    import os
    raw = os.environ.get("ZONE_SHADOW", "0") if value is None else value
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def build_volume_zones(
    candles: list[Any],
    *,
    price: float | None = None,
    bins: int = 24,
    max_zones: int = 6,
    radius_pct: float = 0.10,
) -> list[dict[str, float]]:
    """Build local maxima of smoothed quote volume; last candle is treated as forming."""
    rows = candles[:-1] if len(candles) > 1 else candles
    points: list[tuple[float, float]] = []
    for row in rows:
        high = _get(row, "high", "high_price")
        low = _get(row, "low", "low_price")
        close = _get(row, "close", "close_price")
        volume = _get(row, "quote_volume", "quoteVolume", "turnover")
        if volume is None:
            base = _get(row, "volume", "vol")
            volume = base * close if base is not None and close is not None else None
        if high is None or low is None or close is None or volume is None:
            continue
        if high <= 0 or low <= 0 or high < low or volume <= 0:
            continue
        points.append(((high + low + close) / 3.0, volume))
    if len(points) < 12 or bins < 3:
        return []
    lo = min(p for p, _ in points)
    hi = max(p for p, _ in points)
    if hi <= lo:
        return []
    width = (hi - lo) / bins
    raw = [0.0] * bins
    for p, vol in points:
        idx = min(bins - 1, max(0, int((p - lo) / width)))
        raw[idx] += vol
    smooth = [
        (raw[max(0, i - 1)] + 2.0 * raw[i] + raw[min(bins - 1, i + 1)]) / 4.0
        for i in range(bins)
    ]
    ref = price if price is not None and price > 0 else points[-1][0]
    candidates: list[tuple[float, int]] = []
    for i, value in enumerate(smooth):
        left = smooth[i - 1] if i else -1.0
        right = smooth[i + 1] if i + 1 < bins else -1.0
        center = lo + (i + 0.5) * width
        if value >= left and value >= right and value > 0:
            if abs(center / ref - 1.0) <= radius_pct:
                candidates.append((value, i))
    candidates.sort(reverse=True)
    selected: list[dict[str, float]] = []
    for strength, idx in candidates:
        lower = lo + idx * width
        upper = lower + width
        center = (lower + upper) / 2.0
        if any(abs(center - z["center"]) < width for z in selected):
            continue
        selected.append({
            "lower": lower, "upper": upper, "center": center,
            "quote_volume": strength, "width": width,
        })
        if len(selected) >= max_zones:
            break
    return sorted(selected, key=lambda z: z["center"])


def zone_context(price: float, zones: list[dict[str, float]]) -> dict[str, Any]:
    above = [z for z in zones if z["lower"] > price]
    below = [z for z in zones if z["upper"] < price]
    inside = next((z for z in zones if z["lower"] <= price <= z["upper"]), None)
    def describe(zone: dict[str, float] | None) -> dict[str, float] | None:
        if zone is None:
            return None
        return {
            "lower": round(zone["lower"], 8), "upper": round(zone["upper"], 8),
            "center": round(zone["center"], 8),
            "distance_pct": round((zone["center"] / price - 1.0) * 100.0, 4),
        }
    return {
        "above": describe(min(above, key=lambda z: z["lower"]) if above else None),
        "below": describe(max(below, key=lambda z: z["upper"]) if below else None),
        "in_zone": inside is not None,
        "zone": describe(inside),
    }
