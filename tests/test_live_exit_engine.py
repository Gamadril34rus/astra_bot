import os
from decimal import Decimal
from astra_bot.decision.live_exit_engine import (
    take_price_clamped,
    take_levels,
    qty_split,
    qty_leg_fractions,
    would_regime_exit,
    would_mae_cut,
    trail_stop_candidate,
    max_hold_hit,
    engine_mode,
    ADVERSE_REGIMES,
    PLAN_TP_FRACTIONS,
)
from astra_bot.decision.exit_plan import (
    ADVERSE_REGIMES as PAPER_ADVERSE,
    clamp_take_rr,
    trailing_target,
    structural_tighten,
)
from astra_bot.decision.exit_controller import _atr


def test_contract_same_functions():
    assert ADVERSE_REGIMES is PAPER_ADVERSE or ADVERSE_REGIMES == PAPER_ADVERSE
    assert PLAN_TP_FRACTIONS == (0.3, 0.7)
    # same callables imported
    assert trailing_target is not None
    assert structural_tighten is not None
    assert clamp_take_rr is not None
    assert _atr is not None


def test_default_shadow_cleared_env(monkeypatch):
    monkeypatch.delenv("ZEUS_LIVE_EXIT_ENGINE", raising=False)
    assert engine_mode() == "shadow"


def test_tp1_price_equals_clamped_take():
    e, s = Decimal("100"), Decimal("90")
    # raw 3R → clamp 2.3
    clamped = clamp_take_rr(e, s, Decimal("130"), "long", 2.0, 2.3)
    tp1, tp = take_levels(e, s, "long", take=Decimal("130"))
    assert tp1 == clamped
    assert tp == clamped
    assert take_price_clamped(e, s, "long", Decimal("130")) == clamped


def test_clamp_both_sides():
    e, s = Decimal("100"), Decimal("90")
    # rr 1.0 -> 2.0
    low = take_price_clamped(e, s, "long", Decimal("110"))
    assert abs(float(low) - 120.0) < 1e-6
    # rr 3.0 -> 2.3
    hi = take_price_clamped(e, s, "long", Decimal("130"))
    assert abs(float(hi) - 123.0) < 1e-6


def test_qty_30_70():
    q1, q2 = qty_split(Decimal("10"))
    assert q1 + q2 == Decimal("10")
    assert abs(float(q1 / Decimal("10")) - 0.3) < 1e-9
    assert qty_leg_fractions() == (0.3, 0.7)


def test_trail_only_tightens():
    class B:
        def __init__(self, h, l, c):
            self.high, self.low, self.close = h, l, c
    # need 15 bars
    bars = [B(100, 99, 99.5) for _ in range(14)] + [B(110, 100, 105)]
    cur = Decimal("95")
    # long trail from high 110 - k*atr should be > 95 or None
    out = trail_stop_candidate("long", cur, Decimal("110"), Decimal("99"), bars, k=2.0)
    if out is not None:
        assert out > cur
    # short only tighter
    out_s = trail_stop_candidate("short", Decimal("120"), Decimal("110"), Decimal("100"), bars, k=2.0)
    if out_s is not None:
        assert out_s < Decimal("120")
    # guard short bars
    assert trail_stop_candidate("long", cur, Decimal("110"), None, bars[:5]) is None


def test_max_hold_boundary():
    now = 1_000_000_000_000
    # 47.9h
    opened = now - int(47.9 * 3600 * 1000)
    assert max_hold_hit(opened, now) is False
    # 48.1h
    opened2 = now - int(48.1 * 3600 * 1000)
    assert max_hold_hit(opened2, now) is True


def test_mae_leverage_scale():
    # without lev: threshold -0.9R
    assert would_mae_cut("long", 100, 90, 91.0, leverage=1) is True   # -0.9R: paper uses <=
    assert would_mae_cut("long", 100, 90, 91.1, leverage=1) is False  # -0.89R above threshold
    # with lev>1 scale 0.75 → -0.675R
    # r_now = (99-100)/10 = -0.1 — not enough
    assert would_mae_cut("long", 100, 90, 99, leverage=3) is False
    # r_now = (93.25-100)/10 = -0.675
    assert would_mae_cut("long", 100, 90, 93.25, leverage=3) is True
    assert would_mae_cut("long", 100, 90, 93.25, leverage=1) is False  # needs -0.9
