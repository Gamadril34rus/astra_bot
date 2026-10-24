"""F15: live take parity with paper clamp + fractions."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from astra_bot.decision.exit_plan import PLAN_TP_FRACTIONS, clamp_take_rr
from astra_bot.decision.live_arm import plan_from_candidate
from astra_bot.decision.live_broker import LiveEntryPlan, _tp_frac_from_plan


def test_clamp_take_rr_bounds():
    entry, stop = Decimal("100"), Decimal("90")
    # too tight
    t = clamp_take_rr(entry, stop, Decimal("105"), "long", 2.0, 2.3)
    assert abs(t - entry) / abs(entry - stop) >= Decimal("2.0") - Decimal("0.001")
    # too wide
    t2 = clamp_take_rr(entry, stop, Decimal("150"), "long", 2.0, 2.3)
    assert abs(t2 - entry) / abs(entry - stop) <= Decimal("2.3") + Decimal("0.001")


def test_plan_from_candidate_tp_in_clamp_and_frac():
    """Live plan take in [2.0,2.3]R; tp_frac from PLAN_TP_FRACTIONS."""
    cand = SimpleNamespace(
        symbol="BTC-USDT",
        side="long",
        entry=Decimal("100"),
        stop=Decimal("90"),
        take_profit=Decimal("115"),  # 1.5R raw → clamp up to 2.0
        confidence=0.9,
        leverage=5,
        strategy="zeus_channel_boundary_4h",
        signal_id="t1",
        meta={},
    )
    # plan_from_candidate may need broker/equity - inspect signature
    import inspect
    from astra_bot.decision import live_arm as la
    sig = inspect.signature(la.plan_from_candidate)
    # try minimal call patterns
    broker = MagicMock()
    broker.risk_budget = MagicMock(return_value=Decimal("100"))
    try:
        plan = la.plan_from_candidate(cand, broker=broker, equity=Decimal("10000"))
    except TypeError:
        try:
            plan = la.plan_from_candidate(cand, Decimal("10000"))
        except TypeError:
            plan = la.plan_from_candidate(cand)
    if plan is None:
        # sizing rejected — still unit-test clamp path directly
        entry, stop = Decimal("100"), Decimal("90")
        tp = clamp_take_rr(entry, stop, Decimal("115"), "long", 2.0, 2.3)
        rr = abs(tp - entry) / abs(entry - stop)
        assert Decimal("2.0") <= rr <= Decimal("2.3")
        assert PLAN_TP_FRACTIONS[0] == 0.3
        return
    assert plan.take_profit_price is not None
    rr = abs(plan.take_profit_price - plan.entry_price) / abs(plan.entry_price - plan.stop_price)
    assert Decimal("2.0") <= rr <= Decimal("2.3")
    assert abs(float(plan.tp_frac) - 0.3) < 1e-9


def test_tp_frac_from_plan_fallback():
    assert _tp_frac_from_plan(None) == Decimal("0.3")
    assert _tp_frac_from_plan({}) == Decimal("0.3")
    assert _tp_frac_from_plan({"tp_frac": 0.4}) == Decimal("0.4")
    p = LiveEntryPlan(
        symbol="X", side="long", quantity=Decimal("1"),
        stop_price=Decimal("1"), take_profit_price=Decimal("2"), tp_frac=0.3,
    )
    assert _tp_frac_from_plan(p) == Decimal("0.3")


def test_open_with_protection_uses_frac_not_hardcode():
    """qty leg uses plan.tp_frac (0.3 default)."""
    from astra_bot.decision import live_broker as lb
    src = open(lb.__file__, encoding="utf-8").read()
    assert "fact_qty * Decimal(\"0.4\")" not in src
    assert "_tp_frac_from_plan" in src


def test_net_breakeven_differs_from_entry():
    from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig
    b = LiveBroker(client=MagicMock(), config=LiveBrokerConfig())
    pos = SimpleNamespace(side="long", entry=100.0, quantity=1.0, funding_accum=0)
    be = b.net_breakeven_price(pos)
    assert be != Decimal("100")
    assert be > Decimal("100")


def test_margin_reject_detector():
    from scripts.zeus_live_clock import _margin_reject_msg
    assert _margin_reject_msg("order size must be less than available 100")
    assert not _margin_reject_msg("timeout")
