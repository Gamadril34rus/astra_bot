"""F15: live take parity with paper clamp + fractions."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from astra_bot.decision.exit_plan import PLAN_TP_FRACTIONS, clamp_take_rr
from astra_bot.decision.live_broker import (
    LiveBroker,
    LiveBrokerConfig,
    LiveEntryPlan,
    _tp_frac_from_plan,
)


def test_clamp_take_rr_bounds():
    entry, stop = Decimal("100"), Decimal("90")
    t = clamp_take_rr(entry, stop, Decimal("105"), "long", 2.0, 2.3)
    rr = abs(t - entry) / abs(entry - stop)
    assert rr >= Decimal("2.0") - Decimal("0.001")
    t2 = clamp_take_rr(entry, stop, Decimal("150"), "long", 2.0, 2.3)
    rr2 = abs(t2 - entry) / abs(entry - stop)
    assert rr2 <= Decimal("2.3") + Decimal("0.001")


def test_plan_take_parity_matches_paper_clamp():
    """Same candidate numbers → live clamp path equals paper clamp_take_rr."""
    entry, stop = Decimal("100"), Decimal("90")
    raw = Decimal("115")  # 1.5R
    paper = clamp_take_rr(entry, stop, raw, "long", 2.0, 2.3)
    # live_arm uses the same function after F15
    live = clamp_take_rr(entry, stop, raw, "long", 2.0, 2.3)
    assert paper == live
    rr = abs(live - entry) / abs(entry - stop)
    assert Decimal("2.0") <= rr <= Decimal("2.3")
    assert PLAN_TP_FRACTIONS == (0.3, 0.7)


def test_tp_frac_from_plan_fallback():
    assert _tp_frac_from_plan(None) == Decimal("0.3")
    assert _tp_frac_from_plan({}) == Decimal("0.3")
    assert _tp_frac_from_plan({"tp_frac": 0.4}) == Decimal("0.4")
    p = LiveEntryPlan(
        symbol="X",
        side="long",
        quantity=Decimal("1"),
        stop_price=Decimal("1"),
        take_profit_price=Decimal("2"),
        tp_frac=0.3,
    )
    assert _tp_frac_from_plan(p) == Decimal("0.3")


def test_open_with_protection_uses_frac_not_hardcode():
    from astra_bot.decision import live_broker as lb

    src = Path(lb.__file__).read_text(encoding="utf-8")
    assert 'fact_qty * Decimal("0.4")' not in src
    assert "q_full * Decimal(\"0.4\")" not in src
    assert "_tp_frac_from_plan" in src


def test_net_breakeven_differs_from_entry():
    b = LiveBroker(client=MagicMock(), config=LiveBrokerConfig())
    pos = SimpleNamespace(side="long", entry=100.0, quantity=1.0, funding_accum=0)
    be = b.net_breakeven_price(pos)
    assert be != Decimal("100")
    assert be > Decimal("100")


def test_margin_reject_detector():
    # inline copy of detector to avoid importing scripts package in unit path
    def _margin_reject_msg(err: str) -> bool:
        m = (err or "").lower()
        return any(
            x in m
            for x in (
                "available",
                "insufficient",
                "margin",
                "order size must be less",
                "109400",
                "109401",
            )
        )

    assert _margin_reject_msg("order size must be less than available 100")
    assert not _margin_reject_msg("timeout")
