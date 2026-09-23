"""Confidence → leverage ladder input for Zeus paper (structure quality)."""
from __future__ import annotations

from typing import Any


def structure_confidence(
    *,
    kind: str = "channel",
    width_pct: float | None = None,
    htf_bias: str | None = None,
    direction: str | None = None,
    bars_outside: int | None = None,
    pattern: str | None = None,
) -> float:
    """0.50–0.95: feeds trading_engine.leverage_for (risk $ still from risk_pct)."""
    conf = 0.55
    k = (kind or "").lower()
    if "wedge" in k:
        conf = 0.68
    elif "channel" in k:
        conf = 0.58

    bias = (htf_bias or "").lower()
    d = (direction or "").lower()
    if d == "long" and bias == "up":
        conf += 0.12
    elif d == "short" and bias == "down":
        conf += 0.12
    elif bias in ("up", "down"):
        conf += 0.04

    if width_pct is not None:
        w = float(width_pct)
        if 0.015 <= w <= 0.07:
            conf += 0.08
        elif 0.01 <= w <= 0.10:
            conf += 0.03

    if bars_outside is not None:
        b = int(bars_outside)
        if 2 <= b <= 5:
            conf += 0.05
        elif b in (4, 5, 6):
            conf += 0.04

    pat = (pattern or "").lower()
    if "true_breakout" in pat:
        conf += 0.05
    if "false_break" in pat:
        conf += 0.02

    return float(min(0.95, max(0.50, conf)))


def confidence_from_diag(diag: dict[str, Any], kind: str = "channel") -> float:
    return structure_confidence(
        kind=kind,
        width_pct=diag.get("width_pct"),
        htf_bias=str(diag.get("htf_bias") or ""),
        direction=str(diag.get("direction") or ""),
        bars_outside=diag.get("bars_outside"),
        pattern=str(diag.get("pattern") or ""),
    )
