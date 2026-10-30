"""Early TP1: exchange leg at 0.6R so live MFE band can fill partial."""
from __future__ import annotations

from decimal import Decimal


def test_early_tp1_rr_short_level():
    entry, stop = Decimal("2000"), Decimal("2020")
    risk = abs(entry - stop)
    tp1 = entry - risk * Decimal("0.6")
    assert tp1 == Decimal("1988.0")
    # old F15 clamp-tail at ~2R must NOT be the exchange TP1
    clamp_tail = entry - risk * Decimal("2.0")
    assert clamp_tail == Decimal("1960.0")
    assert tp1 != clamp_tail


def test_early_tp1_rr_long_level():
    entry, stop = Decimal("100"), Decimal("99")
    risk = abs(entry - stop)
    tp1 = entry + risk * Decimal("0.6")
    assert tp1 == Decimal("100.6")
