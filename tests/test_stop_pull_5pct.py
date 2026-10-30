"""Owner 29.10: protective stop pull to lock ~5% profit (pct_profit_pull)."""
from __future__ import annotations

from decimal import Decimal

from astra_bot.decision.live_exit_engine import (
    pct_profit_stop_candidate,
    stop_pull_pct,
)


def test_stop_pull_pct_default(monkeypatch):
    monkeypatch.delenv("ZEUS_STOP_PULL_PCT", raising=False)
    assert abs(stop_pull_pct() - 0.0) < 1e-12  # default off (owner restore working config)
    monkeypatch.setenv("ZEUS_STOP_PULL_PCT", "0.03")
    assert abs(stop_pull_pct() - 0.03) < 1e-12


def test_stop_pull_5pct_long():
    entry, cur = Decimal("100"), Decimal("95")
    # mark not yet +5%
    assert pct_profit_stop_candidate("long", entry, cur, Decimal("104"), pct=0.05) is None
    # mark at +5% → stop to 105
    cand = pct_profit_stop_candidate("long", entry, cur, Decimal("105"), pct=0.05)
    assert cand is not None and abs(float(cand) - 105.0) < 1e-9
    # already tighter than 105
    assert pct_profit_stop_candidate("long", entry, Decimal("106"), Decimal("110"), pct=0.05) is None


def test_stop_pull_5pct_short():
    entry, cur = Decimal("100"), Decimal("105")
    assert pct_profit_stop_candidate("short", entry, cur, Decimal("96"), pct=0.05) is None
    cand = pct_profit_stop_candidate("short", entry, cur, Decimal("95"), pct=0.05)
    assert cand is not None and abs(float(cand) - 95.0) < 1e-9


def test_stop_pull_only_tighten():
    entry = Decimal("100")
    # long: current stop already above lock level → no widen/no move
    assert pct_profit_stop_candidate("long", entry, Decimal("106"), Decimal("110"), pct=0.05) is None
    # short: current stop already below lock → skip
    assert pct_profit_stop_candidate("short", entry, Decimal("94"), Decimal("90"), pct=0.05) is None
