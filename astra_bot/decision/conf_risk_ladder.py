"""Confidence → risk% ladder (pure helpers, no heavy imports)."""
from __future__ import annotations

from decimal import Decimal


def conf_risk_pct(
    conf: float | None,
    ladder: tuple[tuple[float, float], ...] | None,
    base_pct: Decimal = Decimal("0.01"),
) -> Decimal:
    """Map confidence to risk fraction of equity.

    Ladder entries are (min_conf, risk_pct_points) ordered high→low.
    First threshold where threshold <= conf wins; else base_pct (1%).
    """
    if not ladder or conf is None:
        return Decimal(base_pct)
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return Decimal(base_pct)
    for thr, pct_pts in ladder:
        if c >= float(thr):
            return Decimal(str(pct_pts)) / Decimal("100")
    return Decimal(base_pct)
