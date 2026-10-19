"""P3 margin budget gates M2/M3/M4 (owner 19.10, arena numbers).

M2: initial margin of ONE position ≤ 25% equity
M3: sum initial margin of ALL positions ≤ 55% equity
M4: free margin AFTER open ≥ 25% equity

Does NOT change leverage or risk_pct. Fail-open only when equity unknown.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

M2_MAX_POS_FRAC = Decimal("0.25")
M3_MAX_BOOK_FRAC = Decimal("0.55")
M4_MIN_FREE_FRAC = Decimal("0.25")


def initial_margin_usdt(
    entry: Decimal,
    qty: Decimal,
    leverage: int,
) -> Decimal:
    """Notional / leverage (isolated approx)."""
    lev = max(int(leverage or 1), 1)
    notional = abs(entry * qty)
    return notional / Decimal(lev)


def check_margin_gates(
    *,
    equity: Decimal,
    free_margin: Decimal | None,
    used_margin: Decimal | None,
    new_margin: Decimal,
) -> str | None:
    """Return reject reason code or None if OK.

    free_margin/used_margin from exchange when available; else estimate.
    """
    if equity <= 0:
        return None  # fail-open
    if new_margin > equity * M2_MAX_POS_FRAC:
        return "margin_M2"
    used = used_margin if used_margin is not None else Decimal("0")
    if used + new_margin > equity * M3_MAX_BOOK_FRAC:
        return "margin_M3"
    if free_margin is not None:
        if free_margin - new_margin < equity * M4_MIN_FREE_FRAC:
            return "margin_M4"
    else:
        # estimate free = equity - used
        est_free = equity - used
        if est_free - new_margin < equity * M4_MIN_FREE_FRAC:
            return "margin_M4"
    return None


def actual_risk_usdt(qty: Decimal, fill: Decimal, stop: Decimal) -> Decimal:
    return abs(fill - stop) * abs(qty)


def slip_in_r(plan_entry: Decimal, fill: Decimal, stop: Decimal) -> float | None:
    dist = abs(plan_entry - stop)
    if dist <= 0:
        return None
    return float(abs(fill - plan_entry) / dist)
